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
import uuid
from pathlib import Path

from ...core import config
from ...image import elfsym
from ..surfaces import Surfaces, make_surfaces
from . import (
    commands,
    halt,
    hardware,
    history,
    recording,
    regimes,
    snapshot,
    static,
    trace,
)
from .dispatcher import (
    Dispatcher,
    Handler,
    Needs,
    QueryCancelled,
    QueryRejected,
    QuerySlot,
    Request,
)
from .inspector import HaltController
from .poller import ObservationPoller
from .protocol import (
    MAX_BUCKETS,
    Clock,
    Envelopes,
    Kind,
    ProtocolError,
    Src,
    Topic,
    decode_bytes,
    encode,
)
from .session import Deps, Phase, Session, Target, initial_topology
from .store import StateStore
from .trace_drain import TraceDrain

__all__ = [
    "Bridge",
    "Dispatcher",
    "Handler",
    "Needs",
    "ObservationPoller",
    "QueryCancelled",
    "QueryRejected",
    "QuerySlot",
    "Request",
    "TraceDrain",
    "replay",
    "serve",
]

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
        self._closing = False
        self._dispatcher = Dispatcher(
            HANDLERS,
            self._reject,
            self._cancel_publication,
            self.spawn,
        )
        self._queries = self._dispatcher._queries
        self._server = None
        self._flusher: asyncio.Task | None = None
        # The S-layer observation poller manages DWARF provider mapping and S-layer polling.
        self._poller_service = ObservationPoller(
            self.store, self.session, self._board_numbers
        )
        # The T-layer trace drain manages trace ring attachment, draining, and pacing.
        self._trace_service = TraceDrain(
            self.store,
            lambda: self._history,
            self.session,
            self._recorder,
            self._board_numbers,
            self._image_has_tracing,
        )
        # The H-layer halt controller manages GDB/QMP inspection, breakpoints, and stepping.
        self._halt_service = HaltController(
            self.store,
            self.session,
            self._reject,
            self._ensure_poller,
            self.spawn,
        )
        # The bridge's memory of the run. The firmware's rings hold
        # about a second, so this is where the cause of anything noticed
        # late is still findable.
        self._history = history.History(trace_history)
        self._board: dict[str, int] | None = None
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

    async def settled(self) -> None:
        """Wait for the work this bridge has spawned.

        An answer built on a worker is finished after the call that
        asked for it returns, so whoever needs the answer itself waits
        here rather than guessing at a sleep.
        """
        while self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

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
        self._closing = True
        self._queries.clear()
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
                    self._handle_uplink(message, connection)
                except ProtocolError as error:
                    await connection.close(code=1008, reason=str(error)[:120])
                    break
        except Exception:
            # Everything else here is transport: a tab that navigated
            # away, slept, or missed the keepalive deadline ends its
            # connection, which is not a server fault to report.
            pass
        finally:
            self._connections.discard(connection)
            self._disconnect_queries(connection)

    def _handle_uplink(self, message, connection=None) -> None:
        self._dispatcher.handle_uplink(
            self,
            message,
            connection,
            self.session.phase,
            self.session.surfaces,
            self._replay is not None,
            self._closing,
            self._connections,
        )

    def _schedule_query(self, handler: Handler, request: Request) -> None:
        self._dispatcher.schedule_query(
            self, handler, request, self._closing, self._connections
        )

    def _finish_query(self, handler: Handler, request: Request) -> None:
        self._dispatcher.finish_query(
            self, handler, request, self._closing, self._connections
        )

    def _disconnect_queries(self, connection) -> None:
        self._dispatcher.disconnect_queries(connection)

    def _request_live(self, request: Request) -> bool:
        return self._dispatcher.request_live(request, self._closing, self._connections)

    def _reject_query(self, request: Request, reason: str) -> None:
        self._dispatcher.reject_query(request, reason, self._closing, self._connections)

    def _cancel(self, request: Request, reason: str) -> None:
        self._dispatcher.cancel_query(request, reason, self._closing, self._connections)

    def _unmet(self, needs: Needs) -> str | None:
        return self._dispatcher.unmet(
            needs,
            self.session.phase,
            self.session.surfaces,
            self._replay is not None,
        )

    def _send_uart(self, request: Request) -> None:
        reason = self.session.send_bytes(decode_bytes(str(request.data.get("bytes", ""))))
        if reason is not None:
            self._reject(f"uart: {reason}", request.request_id)

    def _select_target(self, request: Request) -> None:
        """Point the session at a demo, and arm before it can run away."""
        data = request.data
        demo = data.get("demo")
        if not demo:
            self._reject("target: missing demo", request.request_id)
            return
        if self.session.phase in (Phase.BUILDING, Phase.VERIFYING):
            # Selects queue on the session lock; accepting one per click
            # would replay every impatient click as a build+teardown.
            self._reject(
                f"target: session is {self.session.phase.value}", request.request_id
            )
            return
        variant = data.get("variant")
        # Hand the outgoing machine back before it is torn down; a run
        # that ends while we hold its stop leaves QEMU frozen mid-exit.
        self._release()
        stops = [str(name) for name in data.get("stops", [])]
        if stops:
            self.spawn(
                self._arm_at_launch(self.session.run_id, stops, request.request_id)
            )
        self.spawn(
            self.session.select(
                Target(
                    demo=str(demo),
                    variant=None if variant is None else str(variant),
                    verify=bool(data.get("verify", False)),
                )
            )
        )

    def _reject(self, reason: str, reply_to: str | None = None) -> None:
        # Window only: a flood of bad uplinks must not evict the replay
        # history every future connection depends on.
        self.store.publish(
            Topic.LIFE,
            Kind.EVENT,
            {"phase": "uplink-rejected", "reason": reason},
            replay=False,
            reply_to=reply_to,
        )

    def _cancel_publication(self, request: Request, reason: str) -> None:
        self.store.publish(
            Topic.LIFE,
            Kind.EVENT,
            {"phase": "query-cancelled", "reason": reason},
            replay=False,
            reply_to=request.request_id,
        )

    def _hold(self) -> halt.HaltInspector:
        """The inspector for this run, made once and kept."""
        return self._halt_service.hold()

    def _release(self) -> None:
        """Give the machine back and forget the inspector."""
        self._halt_service.release()

    async def _sweep_to_panels(self, inspector: halt.HaltInspector) -> None:
        """Everything the machine knows about the instant it stopped."""
        await self._halt_service.sweep_to_panels(inspector)

    @staticmethod
    def _with_delta(value, previous: dict, topic: str) -> dict:
        """A stopped reading, and what moved since the last stop."""
        return HaltController.with_delta(value, previous, topic)

    async def _advance(self, inspector: halt.HaltInspector, data: dict) -> None:
        """Run until a catalogued event, as many times as asked."""
        await self._halt_service.advance(inspector, data)

    async def _arm_at_launch(
        self, previous_run: int, stops: list[str], reply_to: str | None = None
    ) -> None:
        """Take the stop before the guest can reach the event."""
        await self._halt_service.arm_at_launch(previous_run, stops, reply_to)

    def _take_halt(self, request: Request) -> None:
        """Accept one halt command, or say why it is not one."""
        self._halt_service.take_halt(request)

    async def _halt_command(
        self, command: str, data: dict, reply_to: str | None = None
    ) -> None:
        """Pause is a held gdb connection; the machine stays stopped."""
        await self._halt_service.halt_command(command, data, reply_to)

    def _drop_tracer(self) -> None:
        self._history = history.History(self._history.capacity)
        self._halt_service.stopped_at = {}
        self._trace_service.drop()

    def _board_numbers(self) -> dict[str, int]:
        """Board constants, read once. The headers do not change while
        the bridge runs, and the attach probe asks at 200 Hz."""
        if self._board is None:
            self._board = hardware.platform()
        return self._board

    def _set_trace_state(self, state: str, **detail) -> None:
        """Say where the T layer stands, once per transition."""
        self._trace_service.set_trace_state(state, **detail)

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
        symbols = snapshot.image_symbols(self._poller_service.provider)
        return symbols is None or symbols.has(trace.WRITER_SYMBOL)

    def _attach_tracer(self) -> bool:
        return self._trace_service.attach()

    def _drop_tracer(self) -> None:
        self._trace_service.drop()

    def _set_trace_state(self, state: str, **detail) -> None:
        self._trace_service.set_trace_state(state, **detail)

    async def _answer_probe(self, request: Request) -> None:
        """Walk this run's page tables and hand back the map.

        Which bytes are walked is the regime's own answer. What EL2
        built is on the topology, so a replay walks the tables its run
        had by the same code as the run did. A guest's own tables are
        not: they move while it runs, so they are read from RAM at the
        moment the question is asked — which is why a replay can be told
        a guest regime exists and still not be able to walk it.

        Kept out of the connect backlog: every reader asks its own.
        """
        captured = self.store.topology.get("memory")
        if not captured:
            raise QueryRejected("this run has published no page tables")
        answer = await self._answer_off_loop(
            regimes.answer, captured, request.data, self._poller_service.provider
        )
        self._publish_reply(request, Topic.PROBE, answer, Src.SNAP)

    async def _answer_off_loop(self, build, *inputs):
        """Build one answer on a worker while its run remains current.

        The heavy answers a client asks for are tens of milliseconds at
        best and over a second at worst, against a poll every fifty.
        Built on this loop they delay the poll, the drain and every other
        client, which is the same fault the drain's own budget exists to
        prevent.

        A restart while one is being built leaves it describing a machine
        that no longer exists — a map of addresses nothing holds, or a
        window of a run whose history has already been thrown away — so
        the run is checked on the way back, as it is wherever else this
        bridge builds something across an await.
        """
        run = self.session.run_id
        loop = asyncio.get_running_loop()
        try:
            answer = await loop.run_in_executor(None, build, *inputs)
        except (KeyError, ValueError, elfsym.TornRead) as error:
            if self.session.run_id != run:
                raise QueryCancelled("the run changed while the answer was built") from error
            raise QueryRejected(str(error)) from error
        if self.session.run_id != run:
            raise QueryCancelled("the run changed while the answer was built")
        return answer

    def _publish_reply(
        self, request: Request, topic: Topic | str, data: dict, src: Src | str
    ) -> None:
        if not self._request_live(request):
            return
        self.store.publish(
            topic,
            Kind.SNAPSHOT,
            data,
            src=src,
            replay=False,
            reply_to=request.request_id,
        )

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

    def _issue_command(self, request: Request) -> None:
        """Put one command in this run's ring.

        Nothing is published on success: EL2 answers with a trace
        record, so the acknowledgement arrives on the same axis as the
        effects it caused. A refusal is published, because nothing else
        will say it — the command never reached EL2, so no record can
        describe it.
        """
        data = request.data
        name = str(data.get("op", ""))
        if name not in commands.OPS:
            self._reject(f"cmd: unknown op {name!r}", request.request_id)
            return
        try:
            a, b = self._word(data.get("a", 0)), self._word(data.get("b", 0))
        except ValueError as error:
            self._reject(f"cmd: {error}", request.request_id)
            return
        writer = self._poller_service.ensure_writer()
        if writer is None:
            self._reject(
                "cmd: this run has published no command ring", request.request_id
            )
            return
        try:
            writer.issue(commands.OPS[name], a, b)
        except (commands.Full, ValueError, OSError) as error:
            self._reject(f"cmd: {error}", request.request_id)

    async def _answer_cursor(self, request: Request) -> None:
        """Put the whole view at one point in the run.

        One timestamp moves the strip, the panels and the console
        together, because "what did the machine look like then" is one
        question rather than three.

        The table admits it only for a replay: a live machine's now is
        the only point it has, and a panel returned to an earlier reading
        would show a value nothing can be checked against.
        """
        try:
            ts = int(request.data.get("ts"))
        except (TypeError, ValueError):
            raise QueryRejected("ts must be an integer") from None
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
                reply_to=request.request_id,
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
            reply_to=request.request_id,
        )

    async def _answer_window(self, request: Request) -> None:
        """Answer a request for part of the history, at its resolution.

        The request carries how many columns the caller can draw, and
        that single number settles both halves of the answer: the
        density always covers the whole window, and the individual
        records come only when there are few enough to be drawn as
        marks. There is no separate cap to pick and no cliff at which
        marks vanish — more points than pixels is a density by
        definition.
        """
        data = request.data
        if str(data.get("op", "")) != "window":
            raise QueryRejected(f"unknown op {data.get('op')!r}")
        span = self._history.span()
        try:
            first = int(data.get("from", span.first))
            last = int(data.get("to", span.last))
            buckets = int(data.get("buckets", DEFAULT_BUCKETS))
        except (TypeError, ValueError):
            raise QueryRejected("window bounds must be integers") from None
        if not 1 <= buckets <= MAX_BUCKETS:
            # Refused rather than clamped: the response array is as long
            # as this number, and a caller that asked for a million
            # columns has misunderstood something worth telling it about.
            raise QueryRejected(f"buckets must be 1..{MAX_BUCKETS}")
        if last < first:
            raise QueryRejected("window ends before it starts")
        wanted = {str(name) for name in data.get("events", [])}
        # Copied here and decoded elsewhere. The copy has to happen where
        # the drain cannot interleave with it; the decode is a hundred
        # times the cost and is the part that would hold the loop.
        answer = await self._answer_off_loop(
            _window_payload,
            self._history.slice(first, last),
            span.as_dict(),
            self._history.freq_hz,
            first,
            last,
            buckets,
            wanted,
        )
        self._publish_reply(request, Topic.TRACE, answer, Src.TRACE)

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
    def _pump_trace(self) -> bool:
        return self._trace_service.pump()

    def _pace_drain(self, took: float, capped: bool) -> None:
        self._trace_service.pace(took, capped)

    def _drop_provider(self) -> None:
        self._poller_service.drop_provider()

    def _refresh_memory_map(self) -> None:
        self._poller_service.refresh_memory_map()

    def _ensure_writer(self) -> commands.Writer | None:
        return self._poller_service.ensure_writer()

    def _build_provider(self, elf_path: Path, shm_path: Path):
        return self._poller_service._build_provider(elf_path, shm_path)

    async def _ensure_poller(self) -> snapshot.SnapshotPoller | None:
        return await self._poller_service.ensure_poller()

    async def _poll_loop(self) -> None:
        await self._poller_service.loop()

    async def _trace_loop(self) -> None:
        await self._trace_service.loop()

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


def _window_payload(
    packed: bytes,
    span: dict,
    freq_hz: int,
    first: int,
    last: int,
    buckets: int,
    wanted: set[str],
) -> dict:
    """Turn a copied run of records into the answer a window asked for.

    Takes bytes rather than the history, so nothing it touches is being
    written while it works and it can run anywhere. The request carries
    how many columns the caller can draw, and that single number settles
    both halves: the density always covers the whole window, and the
    records themselves come only when there are few enough to be drawn
    as marks. No separate cap to pick, and no cliff at which marks
    vanish — more points than pixels is a density by definition.

    Counted, charted, and conditionally retained in one pass over the
    bytes. Primitive column candidates never exceed `buckets`; a wide
    window drops them as soon as it crosses that boundary.
    """
    count, hist, cols = trace.window(packed, first, last, buckets, wanted)
    payload = {
        # `freq_hz` is the history's, not the reader's that filled it:
        # a replay has no reader.
        "window": {"from": first, "to": last, "n": count, "freq_hz": freq_hz},
        "span": span,
    }
    if cols is None:
        payload["hist"] = hist
    else:
        payload["cols"] = cols
    return payload


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
    Handler(Topic.TRACE, Bridge._answer_window, query=True),
    Handler(Topic.PROBE, Bridge._answer_probe, query=True),
    Handler(Topic.CURSOR, Bridge._answer_cursor, Needs.REPLAY, query=True),
    Handler(Topic.CMD, Bridge._issue_command, Needs.MACHINE),
    Handler(Topic.TARGET, Bridge._select_target, Needs.MACHINE),
    Handler(Topic.HALT, Bridge._take_halt, Needs.RUNNING),
)
UPLINK = frozenset(handler.topic for handler in HANDLERS)
BY_TOPIC = {handler.topic: handler for handler in HANDLERS}


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
