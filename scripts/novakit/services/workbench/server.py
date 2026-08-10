"""The socket edge: one port serves the UI and fans out frames.

Only this module touches websockets — lazily, mirroring the optional
YAML import — and owns the flush cadence. Everything it forwards is a
store frame produced elsewhere, so the wire content stays testable
without a socket.
"""

from __future__ import annotations

import asyncio  # noqa: TID251 — the event loop lives here
import signal
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ...core import config
from ...image import observe
from ..surfaces import Surfaces, make_surfaces
from . import (
    commands,
    halt,
    hardware,
    history,
    observations,
    recording,
    regimes,
    snapshot,
    static,
    trace,
)
from .protocol import (
    MAX_BUCKETS,
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
from .session import Deps, Phase, Session, Target, initial_topology
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
# Arming at launch races the guest, so the watch is tight; the budget
# covers a cold build ahead of the machine it is waiting for.
LAUNCH_POLL_SECONDS = 0.005
LAUNCH_ARM_TIMEOUT_SECONDS = 600.0
# The trace loop's period: the interval that has to fit inside the ring
# depth, set by the peak fill rather than the average, since a guest boot
# bursts to tens of times the run's mean rate. An idle look costs two
# eight-byte reads, because the drain is skipped when nothing is waiting.
TRACE_DRAIN_SECONDS = 0.005
# What one drain may cost the loop it runs on. Stated as a duration
# because that is the thing at stake: a record count would encode how
# fast this machine decodes one, and the ring depth such a count would
# have been chosen against has already moved by sixty-four times once.
# Eight milliseconds is the cost the design accepted for a full ring
# when a full ring was 4096 records; it is now enforced rather than
# assumed, and the allowance in records is found by measurement.
TRACE_TURN_SECONDS = 0.008
# Where the allowance starts and how low it may fall. A floor can only
# make a turn shorter than the budget, never longer, so it cannot bring
# back the stall it is here to prevent.
TRACE_DRAIN_FLOOR = 64
# Columns a window request is answered in, when it does not say. A
# resolution, not a cap on data: the density always covers the whole
# window and only the individual marks are gated on it, because more
# points than pixels is a density by definition.
DEFAULT_BUCKETS = 1200


class Needs(StrEnum):
    """What a handler must be given before it can act, and what the
    reader is told when it is not there.

    One rule per name, stated once. Telling a recording from a machine
    used to be written out at three call sites — twice as a refusal, once
    inverted — and a rule spread over three sites is the rule the fourth
    handler gets written without.
    """

    NOTHING = ""
    # A recording is an observation, not a machine.
    MACHINE = "this is a replay; there is no machine to drive"
    # And one that is up. Stricter than MACHINE and covers it, since a
    # replay is never RUNNING.
    RUNNING = "session is {phase}"
    # The inverse rule: a live machine's now is the only point it has.
    REPLAY = "only a replay can be moved in time"


@dataclass(frozen=True)
class Handler:
    """One uplink topic: what answers it, and what it needs to."""

    topic: Topic
    call: Callable[[Bridge, dict], None]
    needs: Needs = Needs.NOTHING


def _require_websockets():
    try:
        from websockets.asyncio import server as websocket_server  # noqa: TID251
        from websockets.datastructures import Headers  # noqa: TID251
        from websockets.http11 import Response  # noqa: TID251
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
        recorder: recording.Recorder | None = None,
    ):
        # The tee sits on publish(), the single funnel every downlink
        # passes through, and ahead of the frame window — so the file
        # holds everything the wire carried, including what a throttled
        # client never received.
        self._recorder = recorder
        self.store = StateStore(
            Envelopes(Clock()), on_frame=None if recorder is None else recorder.frame
        )
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
        # The run whose S layer ended in a fault, so it is reported
        # once: the fault is a property of the run, not of the tick.
        self._provider_failed: int | None = None
        # Which run's tables have been published. Built once and never
        # rewritten, so one capture per run is all of them.
        self._mapped_run: int | None = None
        # The one writable view of the machine: the command ring's page,
        # and nothing else this process can reach. Per run, like every
        # other mapping of a RAM file that a restart replaces.
        self._writer: commands.Writer | None = None
        self._writer_run: int | None = None
        # The T layer reads the same file but needs no image, so it is
        # built and torn down on its own: a failure to resolve symbols
        # must not take the event stream with it.
        self._tracer: trace.TraceReader | None = None
        self._tracer_run: int | None = None
        # What the ring depth is worth on this host. Built with the
        # geometry, so it dies with the run whose rings it describes.
        self._budget: trace.Budget | None = None
        # How many records one drain may take. Paced against the turn
        # budget as the run reveals what a record costs here.
        self._drain_limit = TRACE_DRAIN_FLOOR
        # What has been said about the T layer this run, so a state is
        # published on the transition rather than on every tick.
        self._trace_state = ""
        self._board: dict[str, int] | None = None
        # The previous stop's reading, per topic. A stop publishes the
        # whole machine; this is what lets it also say what moved.
        self._stopped_at: dict[str, object] = {}
        # The bridge's memory of the run. The firmware's rings hold
        # about a second, so this is where the cause of anything noticed
        # late is still findable.
        self._history = history.History(trace_history)
        # Stamped into every connect topology: a changed token is the
        # restart signal, whatever the sequence counter says.
        self._token = uuid.uuid4().hex[:8]
        # A run read back from disk, if this bridge is showing one.
        self._replay: recording.Recording | None = None
        self._replay_frames: list[dict] = []

    def load_replay(self, rec: recording.Recording) -> None:
        """Show a recorded run instead of a live machine.

        Everything below fills the structures the live bridge already
        answers from — the history the window protocol reads, the
        topology the UI builds itself from — so one run has one answer.
        A replay served by its own code would be a second bridge.
        """
        self._replay = rec
        self.session.phase = Phase.REPLAY
        # Nothing in a replay reads a region header, so the clock the
        # timestamps are in comes from the meta.
        self._history.freq_hz = int(rec.meta.get("freq_hz", 0))
        self._history.append(rec.records)
        # The world the recording was made in — its catalogue, board map
        # and request limits — rather than this process's guesses about a
        # machine that is not here.
        #
        # The last description, not the first: a run republishes its
        # world when what it can witness changes, and the trace rings are
        # placed well after the topology first goes out.
        for frame in reversed(rec.frames):
            if frame.get("topic") == Topic.TOPO.value:
                # The world, without the session state the recorded run
                # merged into it on the way out. Phase, pause and run
                # identity are facts about a connection to a machine,
                # and this connection is to a file — carried over, they
                # would tell the reader the machine is still running.
                session_keys = set(self._live_state())
                world = {
                    key: value
                    for key, value in (frame.get("data") or {}).items()
                    if key not in session_keys
                }
                self.store.adopt_topology(world)
                break
        # Everything but the topology, adopted above. A second one would
        # put the recorded run's phase over the replay's.
        self._replay_frames = [
            frame for frame in rec.frames if frame.get("topic") != Topic.TOPO.value
        ]

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
            # Beside the sockets, so the CLI twin that already globs for
            # a session can ask this bridge for its history instead of
            # reading the firmware's rings over its shoulder.
            self.session.surfaces.port_path.write_text(str(self.port))
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
        self._release()
        await self.session.stop()
        if self._recorder is not None:
            # After the session, so the last frames of a shutdown are in
            # the file the run is judged by.
            self._recorder.close()
            written = self._recorder.written
            total = sum(self._recorder.sizes().values())
            print(
                f"[workbench] recorded {len(written)} run(s) to {self._recorder.root} "
                f"({total / 1e6:.1f} MB): {', '.join(run.name for run in written)}"
            )

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

    def _connect_payload(self) -> list[dict]:
        """What a joining client is given to build the world from.

        Live, that is the topology and a bounded backlog. In a replay it
        is the whole run: a window of the last few hundred frames would
        hand back only the tail of a recording made so the earlier ones
        survive.

        Stamped rather than published, because the payload goes to one
        socket right here; broadcasting it would push the whole run
        through the frame window. Each frame keeps its recorded moment
        and takes this socket's sequence: `ts` belongs to the run, `seq`
        to the ordering of this connection. Kind and src travel verbatim
        — they are what the recorded run wrote, and a value this build
        does not recognise must not be rewritten or refused.
        """
        frames = self.store.connect_frames(self._live_state())
        if self._replay is None:
            return frames
        return frames + [
            self.store.stamp(
                frame.get("topic", ""),
                frame.get("kind", Kind.EVENT.value),
                frame.get("data") or {},
                src=frame.get("src", Src.BRIDGE.value),
                ts=frame.get("ts"),
            )
            for frame in self._replay_frames
        ]

    async def _handler(self, connection) -> None:
        self._connections.add(connection)
        try:
            await connection.send(encode(self._connect_payload()))
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
            uplink = parse_uplink(message, UPLINK)
        except UplinkError as error:
            self._reject(str(error))
            return
        handler = BY_TOPIC[uplink.topic]
        unmet = self._unmet(handler.needs)
        if unmet is not None:
            # Refused with a reason rather than accepted and ignored: a
            # control that silently does nothing is worse than one that
            # says why it cannot.
            self._reject(f"{handler.topic.value}: {unmet}")
            return
        handler.call(self, uplink.data)

    def _unmet(self, needs: Needs) -> str | None:
        """Why this handler cannot act on this session, or None.

        The one place a recording is told apart from a machine, applied
        to whatever the table says needs which.
        """
        if needs is Needs.MACHINE:
            refused = self._replay is not None
        elif needs is Needs.RUNNING:
            refused = self.session.phase is not Phase.RUNNING or self.session.surfaces is None
        elif needs is Needs.REPLAY:
            refused = self._replay is None
        else:
            refused = False
        return needs.value.format(phase=self.session.phase.value) if refused else None

    def _send_uart(self, data: dict) -> None:
        reason = self.session.send_bytes(decode_bytes(str(data.get("bytes", ""))))
        if reason is not None:
            self._reject(f"uart: {reason}")

    def _select_target(self, data: dict) -> None:
        """Point the session at a demo, and arm before it can run away."""
        demo = data.get("demo")
        if not demo:
            self._reject("target: missing demo")
            return
        if self.session.phase in (Phase.BUILDING, Phase.VERIFYING):
            # Selects queue on the session lock; accepting one per click
            # would replay every impatient click as a build+teardown.
            self._reject(f"target: session is {self.session.phase.value}")
            return
        variant = data.get("variant")
        # Hand the outgoing machine back before it is torn down; a run
        # that ends while we hold its stop leaves QEMU frozen mid-exit.
        self._release()
        stops = [str(name) for name in data.get("stops", [])]
        if stops:
            self.spawn(self._arm_at_launch(self.session.run_id, stops))
        self.spawn(
            self.session.select(
                Target(
                    demo=str(demo),
                    variant=None if variant is None else str(variant),
                    verify=bool(data.get("verify", False)),
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
            view = self.session.view
            self._inspector = halt.HaltInspector(
                surfaces.qmp_path, surfaces.gdb_path, None if view is None else view.symbols
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
        was, self._stopped_at = self._stopped_at, {Topic.SYSREG.value: data}
        self.store.publish(
            Topic.SYSREG, Kind.SNAPSHOT, self._with_delta(data, was, Topic.SYSREG.value),
            src=Src.HALT,
        )
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
            self._stopped_at[obs.topic] = value
            self.store.publish(
                obs.topic, Kind.SNAPSHOT, self._with_delta(value, was, obs.topic), src=Src.HALT
            )

    @staticmethod
    def _with_delta(value, previous: dict, topic: str) -> dict:
        """A stopped reading, and what moved since the last stop.

        Absent on the first stop of a run rather than reported as
        "everything changed": there was nothing to change from, and a UI
        that highlighted every field would be pointing at the reading
        itself.
        """
        payload = {"values": value}
        if topic in previous:
            payload["changed"] = snapshot.changed_mask(previous[topic], value)
        return payload

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

    def _take_halt(self, data: dict) -> None:
        """Accept one halt command, or say why it is not one."""
        command = str(data.get("cmd", ""))
        if command not in HALT_COMMANDS:
            self._reject(f"halt: unknown cmd {command!r}")
            return
        self.spawn(self._halt_command(command, data))

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
            await HALT_STEPS[command](self, inspector, data)
        except Exception as error:
            # This coroutine is the request boundary: any protocol fault
            # (bad JSON, corrupt RSP hex, a missing XML attribute) must
            # become a reply, not an unretrieved task exception.
            self._reject(f"halt: {error}")
        finally:
            self._halting = False
            self._abort = False

    async def _halt_stop(self, inspector: halt.HaltInspector, _data: dict) -> None:
        await self._sweep_to_panels(inspector)
        self.store.publish(Topic.LIFE, Kind.EVENT, {"phase": "paused"})

    async def _halt_cont(self, _inspector: halt.HaltInspector, _data: dict) -> None:
        await asyncio.get_running_loop().run_in_executor(None, self._release)
        self.session.paused = False
        self.store.publish(Topic.LIFE, Kind.EVENT, {"phase": "resumed"})

    async def _halt_step(self, inspector: halt.HaltInspector, data: dict) -> None:
        if not inspector.paused:
            await self._sweep_to_panels(inspector)
        count = max(1, min(int(data.get("count", 1)), MAX_STEPS))
        result = await asyncio.get_running_loop().run_in_executor(
            None, inspector.step, count
        )
        self.store.publish(Topic.LIFE, Kind.EVENT, {"phase": "stepped", **result})
        await self._sweep_to_panels(inspector)

    async def _halt_run(self, inspector: halt.HaltInspector, data: dict) -> None:
        if not inspector.paused:
            await self._sweep_to_panels(inspector)
        await self._advance(inspector, data)

    def _drop_tracer(self) -> None:
        tracer, self._tracer = self._tracer, None
        self._tracer_run = None
        self._trace_state = ""
        # Measured against a geometry that is going away.
        self._budget = None
        # A new machine's timestamps are a new epoch, and merging them
        # with the last run's would put the two in one order. The same
        # goes for a stop-to-stop delta across a restart.
        self._history = history.History(self._history.capacity)
        self._stopped_at = {}
        if tracer is not None:
            tracer.close()

    def _board_numbers(self) -> dict[str, int]:
        """Board constants, read once. The headers do not change while
        the bridge runs, and the attach probe asks at 200 Hz."""
        if self._board is None:
            self._board = hardware.platform()
        return self._board

    def _set_trace_state(self, state: str, **detail) -> None:
        """Say where the T layer stands, once per transition."""
        if self._trace_state == state:
            return
        self._trace_state = state
        self.store.publish(Topic.LIFE, Kind.EVENT, {"phase": "trace", "state": state, **detail})

    def _image_has_tracing(self) -> bool:
        """Does this build carry the ring writer?

        Asked of the image rather than inferred from how many times the
        region came back empty: that inference tells a slow machine its
        image has no tracing.

        Read from the S layer's already-parsed index rather than by
        opening the ELF here, because the answer costs a third of a
        second and only changes the wording of a notice. Until that
        index exists the answer is unknown, which is not the same as no,
        so the probing carries on either way.
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
                shm_path,
                board["NOVA_BOARD_PHYS_RAM_BASE"],
                board["NOVA_BOARD_TRACE_PA"],
                board["NOVA_BOARD_TRACE_SIZE"],
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
        # The clock the history's timestamps are in. Known only once a
        # region has been read, and needed by everything that turns a
        # range of them back into a duration.
        self._history.freq_hz = self._tracer.geometry.freq_hz
        self._budget = trace.Budget(
            self._tracer.geometry.capacity, self._tracer.geometry.freq_hz
        )
        # A new machine may decode at a different cost, and the pace
        # this run reached says nothing about the next one.
        self._drain_limit = TRACE_DRAIN_FLOOR
        if self._recorder is not None:
            # A range of counter values is not a duration without this,
            # and a replay needs it before it can answer the first
            # window — so it goes in the meta, not in a frame.
            # The run identity is the recorder's own business — it opens
            # a new recording when the machine restarts — so only the
            # clock is told here.
            self._recorder.note(freq_hz=self._tracer.geometry.freq_hz)
        # Constant for the run, so it rides the transition rather than
        # every summary frame. The geometry travels with it: a stall
        # reported without the depth it ran against cannot be read as a
        # close call or a comfortable one.
        self._set_trace_state(
            "active",
            early=self._tracer.geometry.early,
            rings=self._tracer.geometry.rings,
            capacity=self._tracer.geometry.capacity,
            region_bytes=board["NOVA_BOARD_TRACE_SIZE"],
        )
        # A layer arriving is a change in what the board may claim.
        self.session.regrade_paths(tracing=True)
        return True

    def _answer_probe(self, data: dict) -> None:
        """Walk this run's page tables and hand back the map.

        Answered from the tables on the topology rather than from RAM, so
        a replay walks the bytes its run had by the same code. Kept out
        of the connect backlog: every reader asks its own.
        """
        captured = self.store.topology.get("memory")
        if not captured:
            self._reject("probe: this run has published no page tables")
            return
        try:
            answer = regimes.answer(captured, data)
        except (KeyError, ValueError) as error:
            self._reject(f"probe: {error}")
            return
        self.store.publish(Topic.PROBE, Kind.SNAPSHOT, answer, src=Src.SNAP, replay=False)

    @staticmethod
    def _word(value) -> int:
        """One command argument, as the ring carries it.

        A whole number or nothing. `int()` would take a float and
        truncate it, which is the same quiet reinterpretation EL2
        refuses on its own side rather than narrow.
        """
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"a command argument must be a whole number, not {value!r}")
        return value

    def _issue_command(self, data: dict) -> None:
        """Put one command in this run's ring.

        Nothing is published on success: EL2 answers with a trace
        record, so the acknowledgement arrives on the same axis as the
        effects it caused. A refusal is published, because nothing else
        will say it — the command never reached EL2, so no record can
        describe it.
        """
        name = str(data.get("op", ""))
        if name not in commands.OPS:
            self._reject(f"cmd: unknown op {name!r}")
            return
        try:
            a, b = self._word(data.get("a", 0)), self._word(data.get("b", 0))
        except ValueError as error:
            self._reject(f"cmd: {error}")
            return
        writer = self._ensure_writer()
        if writer is None:
            self._reject("cmd: this run has published no command ring")
            return
        try:
            writer.issue(commands.OPS[name], a, b)
        except (commands.Full, ValueError, OSError) as error:
            self._reject(f"cmd: {error}")

    def _ensure_writer(self) -> commands.Writer | None:
        """This run's write window, opened once the page exists.

        Attempted every poll until it lands, like the page tables: EL2
        places the ring in its last init action, so a window opened with
        the provider would find nothing there. What is placed is
        published, so a reader knows whether this run can be driven
        rather than finding out by trying.
        """
        if self._writer_run == self.session.run_id:
            return self._writer
        symbols = snapshot.image_symbols(self._provider)
        if symbols is None or self.session.surfaces is None:
            # No image behind this provider, so no page to find — and no
            # machine this bridge could be driving either.
            return None
        try:
            page, size = symbols.extent_of(observations.COMMAND_PAGE)
            writer = commands.Writer(
                self.session.surfaces.shm_path,
                self._board_numbers()["NOVA_BOARD_PHYS_RAM_BASE"],
                page,
                size,
            )
        except commands.NotYetFormatted:
            return None  # a moment in a boot; ask again
        except (commands.NotFormatted, KeyError, ValueError) as error:
            # Settled for this run: an image without the ring, or one
            # whose layout this bridge does not understand. Marked as
            # answered so the reason is given once rather than every
            # fiftieth of a second.
            self._writer_run = self.session.run_id
            self._reject(f"cmd: {error}")
            return None
        except OSError:
            return None  # the backend is not readable yet
        self._writer = writer
        self._writer_run = self.session.run_id
        self.session.adopt_command_ring(writer.as_dict())
        return writer

    def _answer_cursor(self, data: dict) -> None:
        """Put the whole view at one point in the run.

        One timestamp moves the strip, the panels and the console
        together, because "what did the machine look like then" is one
        question rather than three.

        The table admits it only for a replay: a live machine's now is
        the only point it has, and a panel returned to an earlier reading
        would show a value nothing can be checked against.
        """
        try:
            ts = int(data.get("ts"))
        except (TypeError, ValueError):
            self._reject("cursor: ts must be an integer")
            return
        wire = self._replay.wire_ts(ts)
        state = self._replay.at(wire)
        # Ordinary snapshot frames, in the shape the panels already
        # apply: a seek-specific payload would teach the client a second
        # way to take a value.
        for frame in state.values():
            self.store.publish(
                frame["topic"],
                Kind.SNAPSHOT,
                frame.get("data") or {},
                src=frame.get("src", Src.BRIDGE.value),
                replay=False,
                ts=frame.get("ts"),
            )
        # Both clocks: the client cuts its console by the wire's and
        # draws its strip by the machine's. Topics with no reading yet at
        # this moment are named, because silence about them would leave
        # their later value on screen.
        self.store.publish(
            Topic.CURSOR,
            Kind.SNAPSHOT,
            {
                "ts": ts,
                "wire": wire,
                "unread": [topic for topic in self._replay.topics if topic not in state],
            },
            replay=False,
        )

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
                # From the history that holds the records, not the
                # reader that filled it: a replay has no reader.
                "freq_hz": self._history.freq_hz,
            },
            "span": span.as_dict(),
        }
        # The records, or the density standing in for them — never both.
        # A density is what a window says when its records will not fit
        # on screen; once they fit they are sent, and a histogram of them
        # is a loop the client can already run over the data it has.
        if len(found) <= buckets:
            payload["cols"] = trace.columns(found, first)
        else:
            payload["hist"] = trace.histogram(found, first, last, buckets)
        self.store.publish(Topic.TRACE, Kind.SNAPSHOT, payload, src=Src.TRACE, replay=False)

    def _pump_trace(self) -> bool:
        """Drain the firmware's rings and publish what fired.

        Returns whether records are still waiting, which is the caller's
        cue to come straight back rather than hold to its tick.

        Counts per path, not the records: a few thousand events a second
        is nothing to the bridge and a great deal to a browser. The
        records stay in the history for a window request to ask for.

        What the drain could not recover arrives as records too, so the
        history holds the holes in the same order and shape as
        everything else, and the summary's loss count is read back off
        them rather than tallied beside them.
        """
        if not self._attach_tracer():
            return False
        # The cheap gate sits here rather than in the loop, so every
        # tick with a reader attached counts as a look: a ring's exposure
        # is the time between opportunities to empty it, and a tick that
        # found it empty is still one.
        #
        # The look is stamped on arrival. Exposure is the stretch a ring
        # went unwatched, which ends when the reader gets there, not
        # when it finishes with what it found — stamping at the end put
        # this turn's own work inside the number that decides whether
        # the ring was deep enough for it.
        arrived = time.monotonic()
        waiting = self._tracer.pending()
        records = self._tracer.drain(limit=self._drain_limit) if waiting else []
        self._budget.looked(records, arrived)
        if not records:
            return False
        self._history.append(records)
        if self._recorder is not None:
            # The records themselves, because the wire carries only the
            # summary: a recording of the frames alone would replay a
            # run whose timeline is empty.
            self._recorder.drained(records)
        self.store.publish(
            Topic.TRACE,
            Kind.EVENT,
            trace.summarise(records)
            | {
                "count": len(records),
                "span": self._history.span().as_dict(),
                "budget": self._budget.as_dict(),
            },
            src=Src.TRACE,
        )
        self._pace_drain(time.monotonic() - arrived, capped=waiting > self._drain_limit)
        return bool(self._tracer.pending())

    def _pace_drain(self, took: float, capped: bool) -> None:
        """Move the allowance so a turn keeps landing inside its budget.

        What a record costs is a property of this machine and this code,
        so it is found rather than declared. A turn that ran over halves
        the allowance; one that came in well under doubles it — but only
        when the allowance was the reason it stopped, or a quiet ring
        would raise it on evidence it never produced.
        """
        if took > TRACE_TURN_SECONDS:
            self._drain_limit = max(TRACE_DRAIN_FLOOR, self._drain_limit // 2)
        elif capped and took < TRACE_TURN_SECONDS / 2:
            self._drain_limit = min(self._tracer.geometry.capacity, self._drain_limit * 2)

    def _drop_provider(self) -> None:
        provider, self._provider = self._provider, None
        writer, self._writer = self._writer, None
        self._poller = None
        self._provider_run = None
        self._mapped_run = None
        self._writer_run = None
        if provider is not None:
            provider.close()
        # The write window maps the same file for the same run, so it
        # goes with it: a mapping outliving its run points into the RAM
        # of a machine that no longer exists.
        if writer is not None:
            writer.close()

    def _capture_memory_map(self) -> None:
        """Copy this run's page tables, once EL2 has built them.

        Once is all of them: they are written during EL2 init and never
        again. Attempted every poll until it lands, because the RAM
        backend exists from the moment QEMU starts and a read before the
        build would copy a page of zeros.
        """
        if self._mapped_run == self.session.run_id or self._provider is None:
            return
        captured = regimes.capture(self._provider, self._provider.regimes)
        if captured is None:
            return
        self._mapped_run = self.session.run_id
        self.session.adopt_memory_map(captured)

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
        if self._provider_failed == current:
            # Ended for this run, and nothing inside a run un-ends it:
            # image, view and backend are all fixed at launch. Retrying
            # republishes the same fault twenty times a second.
            return None
        # A rebuild moves symbols, so a new run needs a new reader.
        self._drop_provider()
        shm_path = session.surfaces.shm_path
        # The backend is created and sized by QEMU; until it is there,
        # there is nothing to map.
        if not shm_path.exists() or shm_path.stat().st_size == 0:
            return None
        built = self._build_provider(session.elf_path, shm_path)
        if session.run_id != current:
            # A restart landed mid-build: this provider maps the
            # previous run's RAM file.
            built.close()
            return None
        self._provider = built
        self._poller = snapshot.SnapshotPoller(built)
        self._provider_run = current
        return self._poller

    def _build_provider(self, elf_path: Path, shm_path: Path):
        """This run's S reader: RAM mapped here, the image already
        answered by the build."""
        view = self.session.view
        if view is None:
            raise observe.Stale(f"no observation view for {elf_path.name}")
        return snapshot.open_provider(
            elf_path, shm_path, self._board_numbers()["NOVA_BOARD_PHYS_RAM_BASE"], view
        )

    async def _poll_loop(self) -> None:
        """Publish S-layer snapshots while a run is live.

        RAM backend races at startup retry silently; any other fault
        ends this run's S layer, is reported once, and never kills the
        loop. The next run starts clean.
        """
        try:
            while True:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                try:
                    poller = await self._ensure_poller()
                    if poller is None:
                        continue
                    self._capture_memory_map()
                    self._ensure_writer()
                    if self.session.paused:
                        # Nothing can change while the machine is
                        # stopped, and the stop already published a full
                        # sweep. Polling static RAM would only spend
                        # reads to confirm it.
                        continue
                    for obs, value in poller.tick():
                        # `ts` is the publisher's CNTPCT for this slot,
                        # on the same clock and in the same units the
                        # trace records carry. A reading can therefore
                        # be placed against the events around it instead
                        # of against the moment this loop got to it,
                        # which is a different quantity by however long
                        # the poll interval and the decode took.
                        payload = {"values": value}
                        stamp = poller.stamp(obs.topic)
                        if stamp is not None:
                            payload["ts"] = stamp
                        self.store.publish(obs.topic, Kind.SNAPSHOT, payload, src=Src.SNAP)
                        # The one reading the topology defers to.
                        if obs.topic == observations.GUEST_TABLE:
                            self.session.adopt_guest_table(value)
                except (FileNotFoundError, snapshot.NotPublishedYet):
                    # The backend vanished mid-step, or EL2 has not
                    # opened its region yet. Both are "not now"; retry.
                    continue
                except Exception as error:
                    self.store.publish(
                        Topic.LIFE,
                        Kind.EVENT,
                        {"phase": "snapshot-unavailable", "error": str(error)},
                    )
                    self._provider_failed = self.session.run_id
                    self._drop_provider()
        finally:
            self._drop_provider()

    async def _trace_loop(self) -> None:
        """Drain the firmware's rings, on the T layer's own clock.

        Separate from the S loop, which costs seconds per DWARF walk: a
        shared loop would put the T layer behind whatever the S layer is
        doing. The two were already independent in what they read; this
        makes them independent in when they read it.

        Draining happens here rather than on a worker. The hand-off
        costs ~122 us against a ~6 us look and takes the GIL twice more
        per tick, so the worker costs more than the work — which holds
        only because a drain is bounded by TRACE_TURN_SECONDS. Reading a
        whole ring in one turn would put the cost of the backlog on this
        loop, and the backlog is how long the last turn took.

        Behind, the loop yields instead of waiting: the tick paces looks
        at a ring that has caught up, and holding to it while records
        are already waiting would only make the next batch bigger.
        """
        try:
            behind = False
            while True:
                # Nothing to hurry for once the image itself says there
                # is no ring to attach to, or that its layout disagrees.
                hurry = self._tracer is not None or self._trace_state in ("", "waiting")
                if behind:
                    await asyncio.sleep(0)
                else:
                    await asyncio.sleep(
                        TRACE_DRAIN_SECONDS if hurry else POLL_INTERVAL_SECONDS
                    )
                if self.session.phase is not Phase.RUNNING:
                    # Yielding is only owed to a backlog this loop is
                    # working through; carrying the debt past the run it
                    # belongs to would spin on a machine that has none.
                    behind = False
                    continue
                try:
                    behind = self._pump_trace()
                except (FileNotFoundError, ValueError):
                    # The backing file went out from under the run.
                    behind = False
                    self._drop_tracer()
        finally:
            self._drop_tracer()

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_INTERVAL_SECONDS)
            if self._recorder is not None:
                # Ahead of the connection check: recording with nobody
                # watching is the ordinary case.
                #
                # And here rather than at the tracer's attach, so a
                # restart is noticed before the new machine's first
                # frame lands in the old machine's file.
                self._recorder.for_run(self.session.run_id)
                self._recorder.flush()
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


# What a client may send: the topic, what answers it, and what the
# session has to be for that answer to mean anything. One row per topic,
# and the accepted set is read off the rows — a topic without a handler
# cannot be admitted, and a handler's precondition is applied by the
# dispatch rather than remembered by whoever writes the next one.
#
# `trace` and `probe` travel both ways. A separate topic for "asking
# about traces" would say the same word twice, and the Kind already
# tells a request from what the bridge sends unasked.
HANDLERS = (
    Handler(Topic.UART, Bridge._send_uart),
    Handler(Topic.TRACE, Bridge._answer_window),
    Handler(Topic.PROBE, Bridge._answer_probe),
    Handler(Topic.CURSOR, Bridge._answer_cursor, Needs.REPLAY),
    Handler(Topic.CMD, Bridge._issue_command, Needs.MACHINE),
    Handler(Topic.TARGET, Bridge._select_target, Needs.MACHINE),
    Handler(Topic.HALT, Bridge._take_halt, Needs.RUNNING),
)
UPLINK = frozenset(handler.topic for handler in HANDLERS)
BY_TOPIC = {handler.topic: handler for handler in HANDLERS}

# What `halt` may ask the stop to do. The vocabulary is the table's keys,
# so a command named in one place and dispatched in another cannot
# happen. `abort` is not among them: it is answered *while* one of these
# is running, so it never takes the inspector.
HALT_STEPS = {
    "stop": Bridge._halt_stop,
    "cont": Bridge._halt_cont,
    "step": Bridge._halt_step,
    "run": Bridge._halt_run,
}
HALT_COMMANDS = ("abort", *HALT_STEPS)


async def _serve_forever(
    *,
    host: str,
    port: int,
    target: Target | None,
    ui_root: Path,
    trace_history: int,
    record: Path | None = None,
) -> None:
    if not ui_root.is_dir():
        raise SystemExit(f"[workbench] UI root missing: {ui_root}")
    surfaces = make_surfaces()
    recorder = None
    if record is not None:
        try:
            recorder = recording.Recorder(
                record,
                {
                    "demo": target.demo if target else None,
                    "variant": target.variant if target else None,
                    "board": hardware.DEFAULT_BOARD,
                },
            )
        except OSError as error:
            # Including a directory that already holds one: somebody's
            # evidence is not something to open with "w".
            raise SystemExit(f"[workbench] {error}") from error
        print(f"[workbench] recording to {recorder.directory}")
    bridge = Bridge(
        ui_root=ui_root, surfaces=surfaces, trace_history=trace_history, recorder=recorder
    )
    # A supervisor's SIGTERM must walk the same teardown as Ctrl-C, or
    # QEMU (its own session, immune to the terminal) outlives the bridge
    # with a gigabyte of tmpfs pinned.
    asyncio.get_running_loop().add_signal_handler(
        signal.SIGTERM, asyncio.current_task().cancel
    )
    try:
        # Topology first: a client racing the startup must never replay
        # an empty world.
        bridge.store.adopt_topology(initial_topology())
        await bridge.open(host, port)
        print(f"[workbench] serving http://{host}:{bridge.port}/ (WebSocket on {WS_PATH})")
        if target is not None:
            bridge.spawn(bridge.session.select(target))
        await asyncio.Future()
    finally:
        await bridge.close()
        surfaces.release()


async def _replay_forever(*, host: str, port: int, ui_root: Path, directory: Path) -> None:
    if not ui_root.is_dir():
        raise SystemExit(f"[workbench] UI root missing: {ui_root}")
    try:
        loaded = recording.load(directory)
    except (recording.Unreadable, OSError) as error:
        raise SystemExit(f"[workbench] {error}") from error
    # No surfaces: there is no machine, so there is nothing to give one.
    # The absence is the point — a replay that needed QEMU would not be
    # a thing you could send somebody.
    bridge = Bridge(ui_root=ui_root)
    asyncio.get_running_loop().add_signal_handler(
        signal.SIGTERM, asyncio.current_task().cancel
    )
    try:
        bridge.store.adopt_topology(initial_topology())
        bridge.load_replay(loaded)
        await bridge.open(host, port)
        meta = loaded.meta
        # Whether the run ended is a fact about the evidence, so it is
        # said where the evidence is described: a truncated recording
        # answers every question a whole one does, right up to the point
        # it stops, and a reader who does not know that reads the end of
        # the file as the end of the run.
        ending = "" if meta.get("complete") else " — INCOMPLETE, the run was killed"
        print(
            f"[workbench] replaying {loaded.directory} — "
            f"{meta.get('demo') or 'unknown demo'}, "
            f"{len(loaded.frames)} frames, {len(loaded.records)} records, "
            f"recorded {meta.get('started', '?')}{ending}"
        )
        print(f"[workbench] serving http://{host}:{bridge.port}/ (WebSocket on {WS_PATH})")
        await asyncio.Future()
    finally:
        await bridge.close()


def replay(
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
    directory: Path,
    ui_root: Path | None = None,
) -> int:
    try:
        asyncio.run(
            _replay_forever(
                host=host,
                port=port,
                ui_root=ui_root or config.WORKBENCH_UI_DIR,
                directory=directory,
            )
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    except OSError as error:
        print(f"nova workbench: cannot serve on {host}:{port}: {error}", file=sys.stderr)
        return 2
    return 0


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
    target: Target | None = None,
    ui_root: Path | None = None,
    trace_history: int = history.DEFAULT_CAPACITY,
    record: Path | None = None,
) -> int:
    try:
        asyncio.run(
            _serve_forever(
                host=host,
                port=port,
                target=target,
                ui_root=ui_root or config.WORKBENCH_UI_DIR,
                trace_history=trace_history,
                record=record,
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
