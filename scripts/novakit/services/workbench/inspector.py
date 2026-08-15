"""The H layer: asynchronous halt, step, advance, and inspection controller."""

from __future__ import annotations

import asyncio  # noqa: TID251 — the event loop lives here
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from . import halt, snapshot
from .dispatcher import Request
from .protocol import Kind, Src, Topic
from .session import Phase

if TYPE_CHECKING:
    from .session import Session
    from .store import StateStore

RUN_SLICE_SECONDS = 0.05
WAIT_NOTICE_SECONDS = 0.5
LAUNCH_POLL_SECONDS = 0.02
LAUNCH_ARM_TIMEOUT_SECONDS = 5.0
MAX_STEPS = 5000


class HaltController:
    """Manages GDB/QMP halt inspection, breakpoints, single-stepping, and state sweeps."""

    def __init__(
        self,
        store: StateStore,
        session: Session,
        reject_fn: Callable[[str, str | None], None],
        ensure_poller_fn: Callable[[], Coroutine[Any, Any, snapshot.SnapshotPoller | None]],
        spawn_fn: Callable[[Coroutine], asyncio.Task],
    ) -> None:
        self.store = store
        self.session = session
        self._reject = reject_fn
        self._ensure_poller = ensure_poller_fn
        self._spawn = spawn_fn

        self.inspector: halt.HaltInspector | None = None
        self.inspector_run = 0
        self.halting = False
        self.abort = False
        # The previous stop's reading, per topic. A stop publishes the
        # whole machine; this is what lets it also say what moved.
        self.stopped_at: dict[str, object] = {}

    def hold(self) -> halt.HaltInspector:
        """The held gdb connection, created if this is the first stop."""
        if self.inspector_run != self.session.run_id:
            self.release()
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
        self.store.publish(
            Topic.SYSREG,
            Kind.SNAPSHOT,
            self.with_delta(data, was, Topic.SYSREG.value),
            src=Src.HALT,
        )
        try:
            poller = await self._ensure_poller()
            if poller is None:
                return
            values = await loop.run_in_executor(None, poller.sweep)
        except Exception as error:
            self.store.publish(
                Topic.LIFE,
                Kind.EVENT,
                {"phase": "snapshot-unavailable", "error": str(error)},
            )
            return
        for obs, value in values:
            self.stopped_at[obs.topic] = value
            self.store.publish(
                obs.topic,
                Kind.SNAPSHOT,
                self.with_delta(value, was, obs.topic),
                src=Src.HALT,
            )

    @staticmethod
    def with_delta(value: Any, previous: dict, topic: str) -> dict:
        """A stopped reading, and what moved since the last stop."""
        payload = {"values": value}
        if topic in previous:
            payload["changed"] = snapshot.changed_mask(previous[topic], value)
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
        if self.halting:
            return
        self.halting = True
        self.abort = False
        try:
            inspector = self.hold()
            await self.sweep_to_panels(inspector)
            self.store.publish(
                Topic.LIFE, Kind.EVENT, {"phase": "armed", "stops": sorted(stops)}
            )
            await self.advance(inspector, {"stops": stops})
        except Exception as error:
            self._reject(f"halt: {error}", reply_to)
        finally:
            self.halting = False
            self.abort = False

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
        if self.halting:
            self._reject("halt: inspection in progress", reply_to)
            return
        self.halting = True
        self.abort = False
        try:
            inspector = self.hold()
            step_fn = HALT_STEPS[command]
            await step_fn(self, inspector, data)
        except Exception as error:
            self._reject(f"halt: {error}", reply_to)
        finally:
            self.halting = False
            self.abort = False

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
            None, inspector.step, count
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
