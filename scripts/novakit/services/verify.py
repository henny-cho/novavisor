"""Run one scenario and leave the same evidence behind, whoever asked.

The demo path and the firmware path used to own a copy of this epilogue each,
and had already drifted apart on where the failure tail goes.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from ..core import board
from . import expect, report, spawn, surfaces
from .workbench import steps


@dataclass(frozen=True)
class Sink:
    """Where a run's output and a failure's evidence go. Console only by default.

    A tail path implies its diagnostics sibling; name `diagnostics` only when
    the file has to sit somewhere else.
    """
    stream: object | None = None
    tail: Path | None = None
    diagnostics: Path | None = None

    def diagnostics_path(self) -> Path | None:
        if self.diagnostics is not None:
            return self.diagnostics
        if self.tail is None:
            return None
        return report.diagnostics_path_for_tail(self.tail)


def announce(scope: str, scenario: expect.Scenario) -> Callable[[expect.StepResult], None]:
    total = len(scenario.steps)

    def carried(step: expect.StepResult) -> None:
        print(f"[{scope}] step[{step.index}/{total}] "
              f"{expect.describe_step(step.kind, step.subject)} "
              f"elapsed={step.elapsed_seconds:.1f}s "
              f"wait={step.waited_seconds:.1f}s "
              f"remaining={step.remaining_seconds:.1f}s")

    return carried


def observable(
    scenario: expect.Scenario,
    opened: surfaces.Surfaces,
    *,
    scope: str,
    on_reading: Callable[[str, object], None] | None = None,
) -> tuple[expect.Scenario, steps.Machine]:
    """Attach a run's surfaces and open the machine behind them.

    Shared with the bridge, which serves verify runs of its own: a
    scenario made observable one way there and another way here would be
    two machines under one name.
    """
    if scenario.elf is None:
        raise SystemExit(
            f"[{scope}] {scenario.label}: steps that observe need the image "
            "that was built, and this scenario carries none"
        )
    attached = replace(scenario, command=tuple(board.attach_workbench(
        list(scenario.command),
        shm_path=opened.shm_path,
        qmp_path=opened.qmp_path,
        gdb_path=opened.gdb_path,
    )))
    return attached, steps.Machine(attached.elf, opened.shm_path, on_reading=on_reading)


def run_scenario(scenario: expect.Scenario, sink: Sink, *, scope: str) -> int:
    """0 when the run showed everything it promised, 1 otherwise."""
    print(f"[{scope}] --- {scenario.label} (phase {scenario.phase}) "
          f"timeout={scenario.timeout_seconds}s ---")

    def persist(capture, result: expect.VerificationResult) -> None:
        report.report_failure(result, scope=scope)
        spawn.preserve_tail(capture, sink.tail, scope=scope)
        diagnostics = sink.diagnostics_path()
        if diagnostics is not None:
            report.write_diagnostics(diagnostics, scenario.label, result)

    # One run's lifetime is this function, so its observation surfaces
    # open and close here. The scenario builder cannot hold them: the
    # soak path builds every variant's scenario before running any, and
    # a surface made there would pin a guest's RAM in tmpfs for the
    # whole soak.
    opened = surfaces.make_surfaces() if expect.needs_observation(scenario.steps) else None
    machine = None
    if opened is not None:
        scenario, machine = observable(scenario, opened, scope=scope)

    try:
        run = spawn.observe(
            scenario,
            stream=sink.stream,
            on_step=announce(scope, scenario),
            handlers=None if machine is None else steps.handlers_for(machine),
        )
    except expect.Interrupted as interrupted:
        persist(interrupted.capture, interrupted.result)
        raise interrupted.cause.with_traceback(interrupted.cause.__traceback__) from None
    finally:
        if machine is not None:
            machine.close()
        if opened is not None:
            opened.release()

    if not run.result.ok:
        persist(run.capture, run.result)
        return 1

    print(f"\n[{scope}] PASS: {scenario.label}")
    return 0
