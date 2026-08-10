"""Run one scenario and leave the same evidence behind, whoever asked.

The demo path and the firmware path used to own a copy of this epilogue each,
and had already drifted apart on where the failure tail goes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import expect, report, spawn


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

    try:
        run = spawn.observe(
            scenario,
            stream=sink.stream,
            on_step=announce(scope, scenario),
        )
    except expect.Interrupted as interrupted:
        persist(interrupted.capture, interrupted.result)
        raise interrupted.cause.with_traceback(interrupted.cause.__traceback__) from None

    if not run.result.ok:
        persist(run.capture, run.result)
        return 1

    print(f"\n[{scope}] PASS: {scenario.label}")
    return 0
