"""Drain firmware trace rings and dynamically pace turnaround budgets."""

from __future__ import annotations

import asyncio  # noqa: TID251 — the event loop lives here
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from . import trace
from .protocol import Kind, Src, Topic
from .session import Phase

if TYPE_CHECKING:
    from .history import History
    from .recording import Recorder
    from .session import Session
    from .store import StateStore

TRACE_DRAIN_SECONDS = 0.005
POLL_INTERVAL_SECONDS = 0.05
TRACE_TURN_SECONDS = 0.008
TRACE_DRAIN_FLOOR = 64


class TraceDrain:
    """Manages trace ring attachment, draining, and dynamic pacing."""

    def __init__(
        self,
        store: StateStore,
        history_fn: Callable[[], History],
        session: Session,
        recorder: Recorder | None,
        board_numbers_fn: Callable[[], dict[str, int]],
        image_has_tracing_fn: Callable[[], bool],
    ) -> None:
        self.store = store
        self._get_history = history_fn
        self.session = session
        self.recorder = recorder
        self._board_numbers = board_numbers_fn
        self._image_has_tracing = image_has_tracing_fn

        self.tracer: trace.TraceReader | None = None
        self.tracer_run: int | None = None
        self.budget: trace.Budget | None = None
        self.drain_limit = TRACE_DRAIN_FLOOR
        self.trace_state = ""

    @property
    def history(self) -> History:
        return self._get_history()

    def drop(self) -> None:
        tracer, self.tracer = self.tracer, None
        self.tracer_run = None
        self.budget = None
        if tracer is not None:
            tracer.close()
            self.set_trace_state("inactive" if self.session.phase is Phase.RUNNING else "")

    def set_trace_state(self, state: str, **detail) -> None:
        if state == self.trace_state:
            return
        self.trace_state = state
        self.store.publish(
            Topic.LIFE,
            Kind.EVENT,
            {"phase": "trace", "state": state, **detail},
            replay=False,
        )

    def attach(self) -> bool:
        """This run's trace reader, opened once the header is formatted."""
        session = self.session
        if session.surfaces is None:
            return False
        if self.tracer_run != session.run_id:
            self.drop()
            self.tracer_run = session.run_id
        if self.tracer is not None:
            return True
        shm_path = session.surfaces.shm_path
        if not shm_path.exists() or shm_path.stat().st_size == 0:
            return False
        board = self._board_numbers()
        try:
            self.tracer = trace.TraceReader(
                shm_path,
                board["NOVA_BOARD_PHYS_RAM_BASE"],
                board["NOVA_BOARD_TRACE_PA"],
                board["NOVA_BOARD_TRACE_SIZE"],
            )
        except (FileNotFoundError, OSError) as error:
            self.set_trace_state("none", reason=str(error))
            return False
        except trace.NotYetFormatted as error:
            self.set_trace_state(
                "waiting" if self._image_has_tracing() else "none", reason=str(error)
            )
            return False
        except trace.NotFormatted as error:
            self.set_trace_state("mismatch", reason=str(error))
            return False

        hist = self.history
        hist.freq_hz = self.tracer.geometry.freq_hz
        self.budget = trace.Budget(
            self.tracer.geometry.capacity, self.tracer.geometry.freq_hz
        )
        self.drain_limit = TRACE_DRAIN_FLOOR
        if self.recorder is not None:
            self.recorder.note(freq_hz=self.tracer.geometry.freq_hz)

        self.set_trace_state(
            "active",
            early=self.tracer.geometry.early,
            rings=self.tracer.geometry.rings,
            capacity=self.tracer.geometry.capacity,
            region_bytes=board["NOVA_BOARD_TRACE_SIZE"],
        )
        self.session.regrade_paths(tracing=True)
        return True

    def pump(self) -> bool:
        """Drain the firmware's rings and publish what fired."""
        if not self.attach() or self.tracer is None or self.budget is None:
            return False
        arrived = time.monotonic()
        waiting = self.tracer.pending()
        records = self.tracer.drain(limit=self.drain_limit) if waiting else []
        self.budget.looked(records, arrived)
        if not records:
            return False
        hist = self.history
        hist.append(records)
        if self.recorder is not None:
            self.recorder.drained(records)
        self.store.publish(
            Topic.TRACE,
            Kind.EVENT,
            trace.summarise(records)
            | {
                "count": len(records),
                "span": hist.span().as_dict(),
                "budget": self.budget.as_dict(),
            },
            src=Src.TRACE,
        )
        self.pace(time.monotonic() - arrived, capped=waiting > self.drain_limit)
        return bool(self.tracer.pending())

    def pace(self, took: float, capped: bool) -> None:
        """Move the allowance so a turn keeps landing inside its budget."""
        if self.tracer is None:
            return
        if took > TRACE_TURN_SECONDS:
            self.drain_limit = max(TRACE_DRAIN_FLOOR, self.drain_limit // 2)
        elif capped and took < TRACE_TURN_SECONDS / 2:
            self.drain_limit = min(self.tracer.geometry.capacity, self.drain_limit * 2)

    async def loop(self) -> None:
        """Drain the firmware's rings on the T layer's own clock."""
        try:
            behind = False
            while True:
                hurry = self.tracer is not None or self.trace_state in ("", "waiting")
                if behind:
                    await asyncio.sleep(0)
                else:
                    await asyncio.sleep(
                        TRACE_DRAIN_SECONDS if hurry else POLL_INTERVAL_SECONDS
                    )
                if self.session.phase is not Phase.RUNNING:
                    behind = False
                    continue
                try:
                    behind = self.pump()
                except (FileNotFoundError, ValueError):
                    behind = False
                    self.drop()
        finally:
            self.drop()
