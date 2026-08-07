"""The socket edge: one port serves the UI and fans out frames.

Only this module touches websockets — lazily, mirroring the optional
YAML import — and owns the flush cadence. Everything it forwards is a
store frame produced elsewhere, so the wire content stays testable
without a socket.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import signal
import sys
import uuid
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from ...core import config
from . import halt, hardware, history, snapshot, static, trace
from .protocol import (
    SUPPORTED_UPLINK,
    Clock,
    Envelopes,
    Kind,
    Src,
    Topic,
    UplinkError,
    decode_bytes,
    encode,
    parse_uplink,
)
from .session import (
    Deps,
    Phase,
    Session,
    Surfaces,
    Target,
    initial_topology,
    make_surfaces,
)
from .store import StateStore

FLUSH_INTERVAL_SECONDS = 0.05
POLL_INTERVAL_SECONDS = 0.05
WS_PATH = "/ws"
# Waiting for a breakpoint is sliced so an abort is answered promptly;
# the machine keeps running across slices, only the listening pauses.
RUN_SLICE_SECONDS = 0.25
# How long to run before saying so. A chosen event may be rare or may
# never occur on this demo; silence reads as a hung bridge.
WAIT_NOTICE_SECONDS = 2.0
# ~700 us per instruction over RSP, so this caps one request at a couple
# of seconds. Stepping is for looking inside an event, not reaching one.
MAX_STEPS = 2000
HALT_COMMANDS = ("stop", "cont", "step", "run", "abort")
# Arming at launch races the guest, so the watch is tight; the budget
# covers a cold build ahead of the machine it is waiting for.
LAUNCH_POLL_SECONDS = 0.005
LAUNCH_ARM_TIMEOUT_SECONDS = 600.0
# The trace loop's period. The rings are a latency budget rather than
# memory, so this is the number that has to fit inside them — and it is
# set by the peak, not the average. Measured on demo 17: the run
# averages ~1500 events/s, but guest boot bursts to ~89k/s, which laps
# a 4096-record ring in 46 ms. Ten times under that, and an idle look
# costs two eight-byte reads because the drain is skipped outright when
# nothing is waiting.
TRACE_DRAIN_SECONDS = 0.005
# Columns a window request is answered in, when it does not say. A
# resolution, not a cap on data: the density always covers the whole
# window and only the individual marks are gated on it, because more
# points than pixels is a density by definition.
DEFAULT_BUCKETS = 1200
# The response arrays are this long, so a caller asking for more than a
# screen's worth of columns is refused rather than quietly given less.
MAX_BUCKETS = 8192


def _require_websockets():
    try:
        from websockets.asyncio import server as websocket_server
        from websockets.datastructures import Headers
        from websockets.http11 import Response
    except ImportError as error:
        raise SystemExit(
            "nova workbench: the pinned websockets package is missing; "
            "run scripts/python-env"
        ) from error
    return websocket_server, Headers, Response


class Bridge:
    """A running bridge: the store, the session, and the serving socket."""

    def __init__(
        self,
        *,
        ui_root: Path,
        deps: Deps | None = None,
        surfaces: Surfaces | None = None,
        trace_history: int = history.DEFAULT_CAPACITY,
    ):
        self.store = StateStore(Envelopes(Clock()))
        self.session = Session(self.store, deps, surfaces)
        self._ui_root = ui_root
        self._connections: set = set()
        self._tasks: set[asyncio.Task] = set()
        self._server = None
        self._flusher: asyncio.Task | None = None
        self._halting = False
        # The gdb connection *is* the stop, so the inspector outlives the
        # command that took it: held while paused, dropped on resume.
        self._inspector: halt.HaltInspector | None = None
        self._inspector_run = 0
        self._abort = False
        # The poll loop owns it; the halt path borrows it to read the
        # whole machine at a stop, where every value is of one instant.
        self._poller: snapshot.SnapshotPoller | None = None
        self._provider: snapshot.SnapshotProvider | None = None
        self._provider_run: int | None = None
        # The T layer reads the same file but needs no image, so it is
        # built and torn down on its own: a failure to resolve symbols
        # must not take the event stream with it.
        self._tracer: trace.TraceReader | None = None
        self._tracer_run: int | None = None
        # What has been said about the T layer this run, so a state is
        # published on the transition rather than on every tick.
        self._trace_state = ""
        self._board: dict[str, int] | None = None
        # The bridge's memory of the run. The firmware's rings are a
        # handover buffer sized in milliseconds; this is where a reader
        # who noticed something late can still find its cause.
        self._history = history.History(trace_history)
        # Where images are parsed. Built on first use and kept, so a
        # restart's re-parse does not pay for a process as well.
        self._images: ProcessPoolExecutor | None = None
        self._images_unavailable = False
        # Stamped into every connect topo: a changed token is the one
        # reliable restart signal, whatever the seq counter says.
        self._token = uuid.uuid4().hex[:8]

    async def open(self, host: str, port: int) -> None:
        websocket_server, headers_type, response_type = _require_websockets()

        def process_request(_connection, request):
            if request.path == WS_PATH:
                return None  # proceed with the WebSocket handshake
            reply = static.resolve(self._ui_root, request.path)
            headers = headers_type()
            headers["Content-Type"] = reply.content_type
            headers["Content-Length"] = str(len(reply.body))
            headers["Connection"] = "close"
            return response_type(reply.status, reply.reason, headers, reply.body)

        self._server = await websocket_server.serve(
            self._handler, host, port, process_request=process_request
        )
        self._flusher = asyncio.create_task(self._flush_loop())
        if self.session.surfaces is not None:
            self.spawn(self._poll_loop())
            self.spawn(self._trace_loop())

    @property
    def port(self) -> int:
        return self._server.sockets[0].getsockname()[1]

    def spawn(self, coroutine) -> None:
        """Run session work without dropping the task reference."""
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._reap)

    def _reap(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            # A worker that dies silently reads as a feature that just
            # stopped; put the loss on the wire where the UI shows it.
            self.store.publish(
                Topic.LIFE, Kind.EVENT, {"phase": "task-failed", "error": str(error)}
            )

    async def close(self) -> None:
        if self._flusher is not None:
            self._flusher.cancel()
        for task in tuple(self._tasks):
            task.cancel()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        if self._images is not None:
            # Not waited on: a parse in flight has nothing left to
            # deliver to, and a supervisor's SIGTERM should not queue
            # behind three seconds of DWARF.
            self._images.shutdown(wait=False, cancel_futures=True)
            self._images = None
        self._release()
        await self.session.stop()

    def _live_state(self) -> dict:
        """Session truth a late joiner cannot recover from the backlog:
        life events are evictable, so phase and pause state ride the
        connect topo instead."""
        return {
            "session": self._token,
            "phase": self.session.phase.value,
            "paused": self.session.paused,
            "run_id": self.session.run_id,
        }

    async def _handler(self, connection) -> None:
        self._connections.add(connection)
        try:
            await connection.send(encode(self.store.connect_frames(self._live_state())))
            async for message in connection:
                try:
                    self._handle_uplink(message)
                except Exception as error:
                    # One unhandled message costs its own reply, never the
                    # connection: a browser that loses its socket also
                    # loses the console it was reading.
                    self._reject(f"uplink failed: {error}")
        except Exception:
            # Everything else here is transport: a tab that navigated
            # away, slept, or missed the keepalive deadline ends its
            # connection, which is not a server fault to report.
            pass
        finally:
            self._connections.discard(connection)

    def _handle_uplink(self, message) -> None:
        if isinstance(message, bytes):
            message = message.decode("utf-8", errors="replace")
        try:
            uplink = parse_uplink(message)
        except UplinkError as error:
            self._reject(str(error))
            return
        if uplink.topic not in SUPPORTED_UPLINK:
            # Recognised but deferred: answer explicitly so the UI
            # degrades visibly instead of losing the command.
            self.store.publish(
                Topic.LIFE,
                Kind.EVENT,
                {"phase": "unsupported", "topic": uplink.topic.value},
            )
            return
        if uplink.topic is Topic.UART:
            reason = self.session.send_bytes(decode_bytes(str(uplink.data.get("bytes", ""))))
            if reason is not None:
                self._reject(f"uart: {reason}")
            return
        if uplink.topic is Topic.TRACE:
            self._answer_window(uplink.data)
            return
        if uplink.topic is Topic.HALT:
            command = str(uplink.data.get("cmd", ""))
            if command not in HALT_COMMANDS:
                self._reject(f"halt: unknown cmd {command!r}")
            elif self.session.phase is not Phase.RUNNING or self.session.surfaces is None:
                self._reject(f"halt: session is {self.session.phase.value}")
            else:
                self.spawn(self._halt_command(command, uplink.data))
            return
        demo = uplink.data.get("demo")
        if not demo:
            self._reject("target: missing demo")
            return
        if self.session.phase in (Phase.BUILDING, Phase.VERIFYING):
            # Selects queue on the session lock; accepting one per click
            # would replay every impatient click as a build+teardown.
            self._reject(f"target: session is {self.session.phase.value}")
            return
        variant = uplink.data.get("variant")
        # Hand the outgoing machine back before it is torn down; a run
        # that ends while we hold its stop leaves QEMU frozen mid-exit.
        self._release()
        stops = [str(name) for name in uplink.data.get("stops", [])]
        if stops:
            self.spawn(self._arm_at_launch(self.session.run_id, stops))
        self.spawn(
            self.session.select(
                Target(
                    demo=str(demo),
                    variant=None if variant is None else str(variant),
                    verify=bool(uplink.data.get("verify", False)),
                )
            )
        )

    def _reject(self, reason: str) -> None:
        # Window only: a flood of bad uplinks must not evict the replay
        # history every future connection depends on.
        self.store.publish(
            Topic.LIFE,
            Kind.EVENT,
            {"phase": "uplink-rejected", "reason": reason},
            replay=False,
        )

    def _hold(self) -> halt.HaltInspector:
        """The inspector for this run, made once and kept.

        Attaching stops the machine, so a fresh inspector per command
        would take a new stop on every click and leak the previous one.
        Keyed on the run: a restart replaces the sockets underneath, and
        an inspector still holding the old ones answers for a machine
        that no longer exists.
        """
        if self._inspector_run != self.session.run_id:
            self._release()
        if self._inspector is None:
            surfaces = self.session.surfaces
            self._inspector = halt.HaltInspector(
                surfaces.qmp_path, surfaces.gdb_path, self.session.elf_path
            )
            self._inspector_run = self.session.run_id
        return self._inspector

    def _release(self) -> None:
        """Give the machine back and forget the inspector.

        Called on resume and at every run boundary: the next run has new
        sockets, and an inspector still holding the old ones would answer
        for a machine that no longer exists.
        """
        inspector, self._inspector = self._inspector, None
        if inspector is not None:
            inspector.resume()

    async def _sweep_to_panels(self, inspector: halt.HaltInspector) -> None:
        """Everything the machine knows about the instant it stopped.

        Registers come from the stub; the rest is the whole observation
        manifest read from a machine that is not moving. Polling has to
        sample and can only report what changed since last time; a
        stopped machine can be read exhaustively, with no torn value and
        no writer racing the reader, so this is the one place the S layer
        is exact. It goes out as H for that reason — same reading, a
        different claim about how much it can be trusted.
        """
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(None, inspector.pause)
        self.session.paused = True
        # Same data shape as the S-layer topics: the UI panels read every
        # snapshot's payload from "values".
        self.store.publish(Topic.SYSREG, Kind.SNAPSHOT, {"values": data}, src=Src.HALT)
        try:
            # Built here if the poll loop has not got to it: arming at
            # launch stops the machine during EL2 boot, long before the
            # first successful poll, and a stop that silently skipped
            # the manifest would be the emptiest one of the run.
            poller = await self._ensure_poller()
            if poller is None:
                return
            values = await loop.run_in_executor(None, poller.sweep)
        except Exception as error:
            self.store.publish(
                Topic.LIFE, Kind.EVENT, {"phase": "snapshot-unavailable", "error": str(error)}
            )
            return
        for obs, value in values:
            self.store.publish(obs.topic, Kind.SNAPSHOT, {"values": value}, src=Src.HALT)

    async def _advance(self, inspector: halt.HaltInspector, data: dict) -> None:
        """Run until a catalogued event, as many times as asked.

        The wait is sliced rather than blocking: a machine that never
        reaches the chosen event would otherwise pin an executor thread
        and ignore the abort button. `period` paces the repeats so the
        reader can watch events at a human speed — the unit of a step is
        an *event*, because a fixed slice of time holds anywhere from
        zero of them to thousands.
        """
        stops = [str(name) for name in data.get("stops", [])]
        period = max(0.0, min(float(data.get("period", 0.0)), 10.0))
        repeat = max(1, min(int(data.get("repeat", 1)), 1000))
        loop = asyncio.get_running_loop()
        for index in range(repeat):
            if self._abort:
                break
            if index and period:
                await asyncio.sleep(period)
                if self._abort:
                    break
            await loop.run_in_executor(None, inspector.begin, stops)
            self.session.paused = False
            stop = None
            waited = 0.0
            noticed = False
            while stop is None and not self._abort:
                stop = await loop.run_in_executor(None, inspector.wait, RUN_SLICE_SECONDS)
                waited += RUN_SLICE_SECONDS
                if stop is None and not noticed and waited >= WAIT_NOTICE_SECONDS:
                    # The chosen event may be rare, or may not happen at
                    # all on this demo. Silence would be indistinguishable
                    # from a hung bridge, so say which stops are pending.
                    noticed = True
                    self.store.publish(
                        Topic.LIFE, Kind.EVENT,
                        {"phase": "waiting", "stops": sorted(inspector.armed)},
                    )
            if stop is None:
                stop = await loop.run_in_executor(None, inspector.interrupt)
            self.session.paused = True
            self.store.publish(
                Topic.LIFE, Kind.EVENT, {"phase": "stopped", **stop.payload()}
            )
            await self._sweep_to_panels(inspector)

    async def _arm_at_launch(self, previous_run: int, stops: list[str]) -> None:
        """Take the stop before the guest can reach the event.

        Short demos are over in well under a second — the DMA demo has
        finished its transfers before a browser could send a command — so
        "stop at the first X" is unreachable if arming has to wait for a
        reader to click. Attaching to the stub stops the machine by
        itself and the socket exists from QEMU's first moments, so taking
        it during EL2 boot arrives long before any guest code runs.
        """
        deadline = asyncio.get_running_loop().time() + LAUNCH_ARM_TIMEOUT_SECONDS
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(LAUNCH_POLL_SECONDS)
            if self.session.phase in (Phase.FAILED, Phase.IDLE, Phase.EXITED):
                return  # the launch did not happen; nothing to arm
            if self.session.run_id == previous_run:
                continue
            surfaces = self.session.surfaces
            if surfaces is None or not surfaces.gdb_path.exists():
                continue
            break
        else:
            self._reject("halt: the machine never came up to be armed")
            return
        if self._halting:
            return  # a reader got there first; theirs wins
        self._halting = True
        self._abort = False
        try:
            inspector = self._hold()
            await self._sweep_to_panels(inspector)
            self.store.publish(
                Topic.LIFE, Kind.EVENT, {"phase": "armed", "stops": sorted(stops)}
            )
            await self._advance(inspector, {"stops": stops})
        except Exception as error:
            self._reject(f"halt: {error}")
        finally:
            self._halting = False
            self._abort = False

    async def _halt_command(self, command: str, data: dict) -> None:
        """Pause is a held gdb connection; the machine stays stopped
        (virtual clock frozen) until it is advanced or resumed."""
        if command == "abort":
            # Checked before the busy guard, not after: an abort is only
            # ever sent *while* an advance is running, so guarding it the
            # same way would reject it exactly when it is needed.
            self._abort = True
            return
        if self._halting:
            self._reject("halt: inspection in progress")
            return
        self._halting = True
        self._abort = False
        try:
            inspector = self._hold()
            loop = asyncio.get_running_loop()
            if command == "stop":
                await self._sweep_to_panels(inspector)
                self.store.publish(Topic.LIFE, Kind.EVENT, {"phase": "paused"})
            elif command == "cont":
                await loop.run_in_executor(None, self._release)
                self.session.paused = False
                self.store.publish(Topic.LIFE, Kind.EVENT, {"phase": "resumed"})
            elif command == "step":
                if not inspector.paused:
                    await self._sweep_to_panels(inspector)
                count = max(1, min(int(data.get("count", 1)), MAX_STEPS))
                result = await loop.run_in_executor(None, inspector.step, count)
                self.store.publish(
                    Topic.LIFE, Kind.EVENT, {"phase": "stepped", **result}
                )
                await self._sweep_to_panels(inspector)
            else:  # "run"
                if not inspector.paused:
                    await self._sweep_to_panels(inspector)
                await self._advance(inspector, data)
        except Exception as error:
            # This coroutine is the request boundary: any protocol fault
            # (bad JSON, corrupt RSP hex, a missing XML attribute) must
            # become a reply, not an unretrieved task exception.
            self._reject(f"halt: {error}")
        finally:
            self._halting = False
            self._abort = False

    def _drop_tracer(self) -> None:
        tracer, self._tracer = self._tracer, None
        self._tracer_run = None
        self._trace_state = ""
        # A new machine's timestamps are a new epoch, and merging them
        # with the last run's would put the two in one order.
        self._history = history.History(self._history.capacity)
        if tracer is not None:
            tracer.close()

    def _board_numbers(self) -> dict[str, int]:
        """Board constants, read once. The headers do not change while
        the bridge runs, and the attach probe asks at 200 Hz."""
        if self._board is None:
            self._board = hardware.platform()
        return self._board

    def _image_pool(self):
        """Where an image gets parsed: another process, if there is one.

        Reading the DWARF is 3.3 s of pure Python, and it runs while the
        guest boots — exactly when the trace rings burst, at ~89k
        events/s into a ring that laps in 46 ms. On a thread it holds
        the GIL through that window; in its own process it competes for
        one of eight cores and for nothing this interpreter needs. The
        measurement that pointed here was blunt: with the S layer off
        entirely, demo 13's loss fell from 24890 records to 250.

        One worker, because there is one image at a time, and it is
        kept: a restart re-parses, and paying to respawn for that would
        put the cost back where it was taken from. If a pool cannot be
        started, the work still happens — on a thread, as before.
        """
        if self._images is None and not self._images_unavailable:
            try:
                self._images = ProcessPoolExecutor(
                    max_workers=1, mp_context=multiprocessing.get_context("forkserver")
                )
            except (OSError, ValueError, ImportError):
                self._images_unavailable = True
        return self._images

    def _set_trace_state(self, state: str, **detail) -> None:
        """Say where the T layer stands, once per transition."""
        if self._trace_state == state:
            return
        self._trace_state = state
        self.store.publish(Topic.LIFE, Kind.EVENT, {"phase": "trace", "state": state, **detail})

    def _image_has_tracing(self) -> bool:
        """Does this build carry the ring writer?

        Asked of the image, which knows, rather than inferred from how
        many times the region has come back empty — that inference told
        a slow machine its image had no tracing, and the tick after it
        called drain() on the None it had just decided on.

        Asked of the S layer's index, which is already parsed, rather
        than by opening the ELF here: the answer costs a third of a
        second to obtain and only ever changes the wording of a notice,
        so paying for it on the attach path would delay the drain to
        improve a log line. Until that index exists the answer is
        unknown — which is not the same as no, so the probing carries
        on either way.
        """
        symbols = snapshot.image_symbols(self._provider)
        return symbols is None or symbols.has(trace.WRITER_SYMBOL)

    def _attach_tracer(self) -> bool:
        """Bind the T reader to this run's region, if it is there yet.

        Two questions, kept apart. Whether the image has a trace layer
        is settled by the image; whether the region has been formatted
        yet is settled by the region, at the cost of a stat and a header
        read. Neither is answered by a retry budget, so nothing here
        latches: the probe is cheap enough to run for the life of a run,
        and an appearing region corrects whatever was said before it.
        """
        session = self.session
        if session.surfaces is None:
            return False
        if self._tracer_run != session.run_id:
            self._drop_tracer()
            self._tracer_run = session.run_id
        if self._tracer is not None:
            return True
        shm_path = session.surfaces.shm_path
        if not shm_path.exists() or shm_path.stat().st_size == 0:
            return False
        board = self._board_numbers()
        try:
            self._tracer = trace.TraceReader(
                shm_path, board["NOVA_BOARD_PHYS_RAM_BASE"], board["NOVA_BOARD_TRACE_PA"]
            )
        except trace.NotYetFormatted as error:
            self._set_trace_state(
                "waiting" if self._image_has_tracing() else "none", reason=str(error)
            )
            return False
        except trace.NotFormatted as error:
            # A region that is there and disagrees about its layout. No
            # amount of asking again resolves a version skew.
            self._set_trace_state("mismatch", reason=str(error))
            return False
        # Constant for the run, so it rides the transition rather than
        # every summary frame.
        self._set_trace_state("active", early=self._tracer.geometry.early)
        # A layer arriving is a change in what the board may claim.
        self.session.regrade_paths(tracing=True)
        return True

    def _answer_window(self, data: dict) -> None:
        """Answer a request for part of the history, at its resolution.

        The request carries how many columns the caller can draw, and
        that single number settles both halves of the answer: the
        density always covers the whole window, and the individual
        records come only when there are few enough to be drawn as
        marks. There is no separate cap to pick and no cliff at which
        marks vanish — more points than pixels is a density by
        definition.
        """
        if str(data.get("op", "")) != "window":
            self._reject(f"trace: unknown op {data.get('op')!r}")
            return
        span = self._history.span()
        try:
            first = int(data.get("from", span.first))
            last = int(data.get("to", span.last))
            buckets = int(data.get("buckets", DEFAULT_BUCKETS))
        except (TypeError, ValueError):
            self._reject("trace: window bounds must be integers")
            return
        if not 1 <= buckets <= MAX_BUCKETS:
            # Refused rather than clamped: the response array is as long
            # as this number, and a caller that asked for a million
            # columns has misunderstood something worth telling it about.
            self._reject(f"trace: buckets must be 1..{MAX_BUCKETS}")
            return
        if last < first:
            self._reject("trace: window ends before it starts")
            return
        wanted = {str(name) for name in data.get("events", [])}
        found = self._history.window(first, last)
        if wanted:
            found = [record for record in found if record.event in wanted]
        payload = {
            "window": {
                "from": first,
                "to": last,
                "n": len(found),
                "freq_hz": self._tracer.geometry.freq_hz if self._tracer else 0,
            },
            "span": span.as_dict(),
        }
        # The records, or the density that stands in for them — never
        # both. A density is what a window says when its records will
        # not fit on the screen; once they do fit, they are sent, and
        # any histogram of them is a loop the client already has the
        # data for. Sending both put 1200 mostly-zero buckets beside
        # four marks, on the request shape live following uses most.
        if len(found) <= buckets:
            payload["cols"] = trace.columns(found, first)
        else:
            payload["hist"] = trace.histogram(found, first, last, buckets)
        self.store.publish(Topic.TRACE, Kind.SNAPSHOT, payload, src=Src.TRACE, replay=False)

    def _pump_trace(self) -> None:
        """Drain the firmware's rings and publish what fired.

        Counts per path, not the records: a few thousand events a second
        is nothing to the bridge and a great deal to a browser, and a cap
        with a silent drop would make "everything that happened" a lie.
        The records stay here for `nova workbench trace` to ask for.
        """
        if not self._attach_tracer():
            return
        records, lost = self._tracer.drain()
        if not records and not lost:
            return
        self._history.append(records)
        self.store.publish(
            Topic.TRACE,
            Kind.EVENT,
            trace.summarise(records)
            | {"dropped": lost, "count": len(records), "span": self._history.span().as_dict()},
            src=Src.TRACE,
        )

    def _drop_provider(self) -> None:
        provider, self._provider = self._provider, None
        self._poller = None
        self._provider_run = None
        if provider is not None:
            provider.close()

    async def _ensure_poller(self) -> snapshot.SnapshotPoller | None:
        """The S reader for this run, built once and shared.

        Both the poll loop and the halt path need it, and a stop can
        arrive before the loop's first successful build — arming at
        launch stops the machine during EL2 boot, well ahead of the
        first poll — so whoever needs it first builds it. Returns None
        while the backend is not ready yet; the caller retries.
        """
        session = self.session
        if session.phase is not Phase.RUNNING or session.elf_path is None:
            return None
        if session.surfaces is None:
            return None
        current = session.run_id
        if self._provider_run == current:
            return self._poller
        # A rebuild moves symbols, so a new run needs a new reader.
        self._drop_provider()
        shm_path = session.surfaces.shm_path
        # Cheap gate: no per-retry DWARF walk while QEMU is still
        # creating and sizing the backend.
        if not shm_path.exists() or shm_path.stat().st_size == 0:
            return None
        built = await self._build_provider(session.elf_path, shm_path)
        if session.run_id != current:
            # A restart landed mid-build: this provider maps the
            # previous run's RAM file.
            built.close()
            return None
        self._provider = built
        self._poller = snapshot.SnapshotPoller(built)
        self._provider_run = current
        return self._poller

    async def _build_provider(self, elf_path: Path, shm_path: Path):
        """This run's S reader: the image parsed elsewhere, RAM mapped
        here. Split so the expensive half can leave this process."""
        view = await asyncio.get_running_loop().run_in_executor(
            self._image_pool(), snapshot.resolve_image, elf_path
        )
        return snapshot.ElfRamProvider(
            elf_path, shm_path, self._board_numbers()["NOVA_BOARD_PHYS_RAM_BASE"], view
        )

    async def _poll_loop(self) -> None:
        """Publish S-layer snapshots while a run is live.

        Construction — one full DWARF walk — happens in another process.
        RAM backend races at startup retry silently; any other fault ends
        this run's S layer, is reported once, and never kills the loop.
        """
        try:
            while True:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                try:
                    poller = await self._ensure_poller()
                    if poller is None:
                        continue
                    if self.session.paused:
                        # Nothing can change while the machine is
                        # stopped, and the stop already published a full
                        # sweep. Polling static RAM would only spend
                        # reads to confirm it.
                        continue
                    for obs, value in poller.tick():
                        self.store.publish(
                            obs.topic,
                            Kind.SNAPSHOT,
                            {"values": value},
                            src=Src.SNAP,
                        )
                except FileNotFoundError:
                    continue  # the backend vanished mid-step; retry next tick
                except Exception as error:
                    self.store.publish(
                        Topic.LIFE,
                        Kind.EVENT,
                        {"phase": "snapshot-unavailable", "error": str(error)},
                    )
                    self._drop_provider()
        finally:
            self._drop_provider()

    async def _trace_loop(self) -> None:
        """Drain the firmware's rings, on the T layer's own clock.

        Separate from the S loop on purpose. The two have very different
        costs — one DWARF walk is three seconds — and a shared loop puts
        the T layer behind whatever the S layer is doing. They were
        already independent in what they read; this makes them
        independent in when they read it, at their own rates.

        Draining happens right here rather than on a worker. Measured,
        the hand-off is 122 us against a 6 us look, and it needs the GIL
        twice more per tick at a period the interpreter hands the GIL
        over at — so the worker cost more than the work. A full ring is
        ~8 ms of loop time, which the 50 ms flush absorbs.
        """
        try:
            while True:
                # Nothing to hurry for once the image itself says there
                # is no ring to attach to, or that its layout disagrees.
                hurry = self._tracer is not None or self._trace_state in ("", "waiting")
                await asyncio.sleep(TRACE_DRAIN_SECONDS if hurry else POLL_INTERVAL_SECONDS)
                if self.session.phase is not Phase.RUNNING:
                    continue
                try:
                    if self._tracer is not None and self._tracer.pending() == 0:
                        continue
                    self._pump_trace()
                except (FileNotFoundError, ValueError):
                    # The backing file went out from under the run.
                    self._drop_tracer()
        finally:
            self._drop_tracer()

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_INTERVAL_SECONDS)
            if not self._connections:
                # Leave frames in the window: the first joiner gets them
                # on the next flush instead of a silent discard.
                continue
            frames = self.store.drain()
            if not frames:
                continue
            payload = encode(frames)
            await asyncio.gather(
                *(self._send(connection, payload) for connection in tuple(self._connections))
            )

    async def _send(self, connection, payload: str) -> None:
        # One stalled client (suspended laptop, throttled tab) must not
        # hold every other client's frames hostage; a second of
        # backpressure forfeits the connection.
        try:
            await asyncio.wait_for(connection.send(payload), timeout=1.0)
        except Exception:
            self._connections.discard(connection)
            try:
                await asyncio.wait_for(connection.close(code=1011), timeout=1.0)
            except Exception:
                pass  # the transport is already beyond a clean close


async def _serve_forever(
    *, host: str, port: int, target: Target | None, ui_root: Path, trace_history: int
) -> None:
    if not ui_root.is_dir():
        raise SystemExit(f"[workbench] UI root missing: {ui_root}")
    surfaces = make_surfaces()
    bridge = Bridge(ui_root=ui_root, surfaces=surfaces, trace_history=trace_history)
    # A supervisor's SIGTERM must walk the same teardown as Ctrl-C, or
    # QEMU (its own session, immune to the terminal) outlives the bridge
    # with a gigabyte of tmpfs pinned.
    asyncio.get_running_loop().add_signal_handler(
        signal.SIGTERM, asyncio.current_task().cancel
    )
    try:
        # Topology first: a client racing the startup must never replay
        # an empty world.
        bridge.store.set_topology(initial_topology())
        await bridge.open(host, port)
        print(f"[workbench] serving http://{host}:{bridge.port}/ (WebSocket on {WS_PATH})")
        if target is not None:
            bridge.spawn(bridge.session.select(target))
        await asyncio.Future()
    finally:
        await bridge.close()
        surfaces.release()


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
    target: Target | None = None,
    ui_root: Path | None = None,
    trace_history: int = history.DEFAULT_CAPACITY,
) -> int:
    try:
        asyncio.run(
            _serve_forever(
                host=host,
                port=port,
                target=target,
                ui_root=ui_root or config.WORKBENCH_UI_DIR,
                trace_history=trace_history,
            )
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass  # Ctrl-C and SIGTERM both unwound through the finally
    except OSError as error:
        # The bind is the only OS surface before the loop settles; a
        # taken port deserves one line, not a traceback.
        print(f"nova workbench: cannot serve on {host}:{port}: {error}", file=sys.stderr)
        return 2
    return 0
