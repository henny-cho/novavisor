"""The H layer: asynchronous halt, step, advance, and inspection controller."""

from __future__ import annotations

import asyncio  # noqa: TID251 — the event loop lives here
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from . import halt, snapshot
from .dispatcher import Request
from .protocol import MAX_STEPS, Kind, Src, Topic
from .session import Phase

if TYPE_CHECKING:
    from .session import Session
    from .store import StateStore

# Waiting for a breakpoint is sliced so an abort is answered promptly;
# the machine keeps running across slices, only the listening pauses.
RUN_SLICE_SECONDS = 0.05
# How long to run before saying so. A chosen event may be rare or may
# never occur on this demo; silence reads as a hung bridge.
WAIT_NOTICE_SECONDS = 0.5
# Arming at launch races the guest, so the watch is tight; the budget
# covers a cold build ahead of the machine it is waiting for.
LAUNCH_POLL_SECONDS = 0.02
LAUNCH_ARM_TIMEOUT_SECONDS = 600.0


class HaltController:
    """Manages GDB/QMP halt inspection, breakpoints, single-stepping, and state sweeps."""

    def __init__(
        self,
        store: StateStore,
        session: Session,
        reject_fn: Callable[[str, str | None], None],
        ensure_poller_fn: Callable[[], Coroutine[Any, Any, snapshot.SnapshotPoller | None]],
        newest_ts_fn: Callable[[], int | None],
        spawn_fn: Callable[[Coroutine], asyncio.Task],
    ) -> None:
        self.store = store
        self.session = session
        self._reject = reject_fn
        self._ensure_poller = ensure_poller_fn
        self._newest_ts = newest_ts_fn
        self._spawn = spawn_fn

        self.inspector: halt.HaltInspector | None = None
        self.inspector_run = 0
        # The command the bridge holds the machine for, or None. Reported
        # at both ends so controls follow it instead of what they sent.
        self.flight: dict | None = None
        self.abort = False
        # The previous stop's reading, per topic. A stop publishes the
        # whole machine; this is what lets it also say what moved.
        self.stopped_at: dict[str, object] = {}

    def hold(self) -> halt.HaltInspector:
        """The held gdb connection, created if this is the first stop."""
        if self.inspector_run != self.session.run_id:
            self.release()
            # The delta's baseline is a stop of *this* run; against the
            # last machine's it would report registers nothing moved.
            self.stopped_at = {}
        if self.inspector is None:
            surfaces = self.session.surfaces
            if surfaces is None:
                raise RuntimeError("no active session surfaces")
            view = self.session.view
            self.inspector = halt.HaltInspector(
                surfaces.qmp_path, surfaces.gdb_path, None if view is None else view.symbols
            )
            self.inspector_run = self.session.run_id
        return self.inspector

    def release(self) -> None:
        """Give the machine back and forget the inspector."""
        inspector, self.inspector = self.inspector, None
        if inspector is not None:
            inspector.resume()

    async def sweep_to_panels(self, inspector: halt.HaltInspector) -> None:
        """Everything the machine knows about the instant it stopped."""
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(None, inspector.pause)
        self.session.paused = True
        was, self.stopped_at = self.stopped_at, {Topic.SYSREG.value: data}
        poller = None
        try:
            poller = await self._ensure_poller()
        except Exception as error:
            self._unavailable(error)
        at = self.stop_floor(poller)
        self.store.publish(
            Topic.SYSREG,
            Kind.SNAPSHOT,
            self.with_delta(data, was, Topic.SYSREG.value, at),
            src=Src.HALT,
        )
        if poller is None:
            return
        try:
            values = await loop.run_in_executor(None, poller.sweep)
        except Exception as error:
            self._unavailable(error)
            return
        for obs, value in values:
            self.stopped_at[obs.topic] = value
            self.store.publish(
                obs.topic,
                Kind.SNAPSHOT,
                self.with_delta(value, was, obs.topic, at),
                src=Src.HALT,
            )

    def _unavailable(self, error: Exception) -> None:
        self.store.publish(
            Topic.LIFE, Kind.EVENT, {"phase": "snapshot-unavailable", "error": str(error)}
        )

    def stop_floor(self, poller: snapshot.SnapshotPoller | None) -> int | None:
        """The machine's clock as of the stop, from below.

        The instant itself is unreadable — QEMU's stub exposes no counter —
        so the freshest firmware timestamp in the frozen memory stands in:
        the newest ring record, or the newest published slot without a ring.
        """
        floors = [self._newest_ts(), None if poller is None else poller.newest_stamp()]
        known = [floor for floor in floors if floor is not None]
        return max(known) if known else None

    @staticmethod
    def with_delta(value: Any, previous: dict, topic: str, at: int | None) -> dict:
        """A stopped reading, what moved since the last stop, and when it is of."""
        payload = {"values": value}
        if topic in previous:
            payload["changed"] = snapshot.changed_mask(previous[topic], value)
        if at is not None:
            payload["ts"] = at
        return payload

    async def advance(self, inspector: halt.HaltInspector, data: dict) -> None:
        """Run until a catalogued event, as many times as asked."""
        stops = [str(name) for name in data.get("stops", [])]
        period = max(0.0, min(float(data.get("period", 0.0)), 10.0))
        repeat = max(1, min(int(data.get("repeat", 1)), 1000))
        loop = asyncio.get_running_loop()
        for index in range(repeat):
            if self.abort:
                break
            if index and period:
                await asyncio.sleep(period)
                if self.abort:
                    break
            await loop.run_in_executor(None, inspector.begin, stops)
            self.session.paused = False
            # Every resume, not only the one at launch: between repeats
            # the machine runs again and the reader's pause state must say so.
            self.store.publish(
                Topic.LIFE, Kind.EVENT, {"phase": "armed", "stops": inspector.armed}
            )
            stop = None
            waited = 0.0
            noticed = False
            while stop is None and not self.abort:
                stop = await loop.run_in_executor(None, inspector.wait, RUN_SLICE_SECONDS)
                waited += RUN_SLICE_SECONDS
                if stop is None and not noticed and waited >= WAIT_NOTICE_SECONDS:
                    noticed = True
                    self.store.publish(
                        Topic.LIFE,
                        Kind.EVENT,
                        {"phase": "waiting", "stops": sorted(inspector.armed)},
                    )
            if stop is None:
                stop = await loop.run_in_executor(None, inspector.interrupt)
            self.session.paused = True
            self.store.publish(
                Topic.LIFE, Kind.EVENT, {"phase": "stopped", **stop.payload()}
            )
            await self.sweep_to_panels(inspector)

    async def arm_at_launch(
        self, previous_run: int, stops: list[str], reply_to: str | None = None
    ) -> None:
        """Take the stop before the guest can reach the event."""
        deadline = asyncio.get_running_loop().time() + LAUNCH_ARM_TIMEOUT_SECONDS
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(LAUNCH_POLL_SECONDS)
            if self.session.phase in (Phase.FAILED, Phase.IDLE, Phase.EXITED):
                return
            if self.session.run_id == previous_run:
                continue
            surfaces = self.session.surfaces
            if surfaces is None or not surfaces.gdb_path.exists():
                continue
            break
        else:
            self._reject("halt: the machine never came up to be armed", reply_to)
            return
        if self.flight is not None:
            return

        async def arm_and_run(inspector: halt.HaltInspector) -> None:
            await self.sweep_to_panels(inspector)
            await self.advance(inspector, {"stops": stops})

        await self._inspect("run", arm_and_run, reply_to)

    def take_halt(self, request: Request) -> None:
        """Accept one halt command, or say why it is not one."""
        data = request.data
        command = str(data.get("cmd", ""))
        if command not in HALT_COMMANDS:
            self._reject(f"halt: unknown cmd {command!r}", request.request_id)
            return
        self._spawn(self.halt_command(command, data, request.request_id))

    async def halt_command(
        self, command: str, data: dict, reply_to: str | None = None
    ) -> None:
        """Pause is a held gdb connection; the machine stays stopped."""
        if command == "abort":
            self.abort = True
            return
        if self.flight is not None:
            self._reject("halt: inspection in progress", reply_to)
            return
        step_fn = HALT_STEPS[command]
        await self._inspect(command, lambda inspector: step_fn(self, inspector, data), reply_to)

    async def _inspect(
        self,
        command: str,
        body: Callable[[halt.HaltInspector], Coroutine],
        reply_to: str | None,
    ) -> None:
        """One inspection at a time, both ends on the wire.

        An advance outlasts its click by minutes and answers many times
        before the bridge lets go, so the controls follow these two
        events rather than the request they remember sending.
        """
        self.flight = {"cmd": command}
        self.abort = False
        self.store.publish(Topic.LIFE, Kind.EVENT, {"phase": "halt-begin", **self.flight})
        try:
            await body(self.hold())
        except Exception as error:
            self._reject(f"halt: {error}", reply_to)
        finally:
            self.flight = None
            self.abort = False
            self.store.publish(Topic.LIFE, Kind.EVENT, {"phase": "halt-end"})

    async def halt_stop(self, inspector: halt.HaltInspector, _data: dict) -> None:
        await self.sweep_to_panels(inspector)
        self.store.publish(Topic.LIFE, Kind.EVENT, {"phase": "paused"})

    async def halt_cont(self, _inspector: halt.HaltInspector, _data: dict) -> None:
        await asyncio.get_running_loop().run_in_executor(None, self.release)
        self.session.paused = False
        self.store.publish(Topic.LIFE, Kind.EVENT, {"phase": "resumed"})

    async def halt_step(self, inspector: halt.HaltInspector, data: dict) -> None:
        if not inspector.paused:
            await self.sweep_to_panels(inspector)
        count = max(1, min(int(data.get("count", 1)), MAX_STEPS))
        result = await asyncio.get_running_loop().run_in_executor(
            None, inspector.step, count, lambda: self.abort
        )
        self.store.publish(Topic.LIFE, Kind.EVENT, {"phase": "stepped", **result})
        await self.sweep_to_panels(inspector)

    async def halt_run(self, inspector: halt.HaltInspector, data: dict) -> None:
        if not inspector.paused:
            await self.sweep_to_panels(inspector)
        await self.advance(inspector, data)


HALT_STEPS = {
    "stop": HaltController.halt_stop,
    "cont": HaltController.halt_cont,
    "step": HaltController.halt_step,
    "run": HaltController.halt_run,
}
HALT_COMMANDS = ("abort", *HALT_STEPS)
