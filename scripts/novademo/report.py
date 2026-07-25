"""Failure reporting: artifact naming, diagnostics files, and console output.

Every filename convention and diagnostics schema a failed run leaves behind is
owned here, so CI can rely on one place defining them.
"""

from __future__ import annotations

import csv
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .console import (
    FailureKind,
    OutputCapture,
    RepeatAttempt,
    VerificationResult,
    preserve_failure_tail,
)

if TYPE_CHECKING:  # Type-only: report never depends on command wiring.
    from .commands import PreparedVerification


def diagnostics_path_for_tail(tail_path: Path) -> Path:
    suffix = ".qemu-tail.log"
    name = tail_path.name
    if name.endswith(suffix):
        name = name[:-len(suffix)]
    return tail_path.with_name(f"{name}.diagnostics.json")


@dataclass(frozen=True)
class ArtifactPaths:
    """Single owner of every failure-artifact filename convention."""
    root: Path

    @classmethod
    def from_arg(cls, arg: str | None) -> "ArtifactPaths | None":
        if not arg:
            return None
        paths = cls(Path(arg))
        paths.initialize()
        return paths

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for pattern in ("*.qemu-tail.log", "*.diagnostics.json"):
            for stale in self.root.glob(pattern):
                if stale.is_file():
                    stale.unlink()

    def verify_tail(self, name: str, variant: int) -> Path:
        return self.root / f"{name}-variant-{variant:02d}.qemu-tail.log"

    def repeat_tail(self, attempt: int, variant: int) -> Path:
        return self.root / f"attempt-{attempt:02d}-variant-{variant:02d}.qemu-tail.log"


def preserve_failure_diagnostics(
    capture: OutputCapture,
    tail_path: Path | None,
    prepared: "PreparedVerification",
    result: VerificationResult,
) -> None:
    preserve_failure_tail(capture, tail_path)
    if tail_path is None:
        return

    diagnostics = {
        "label": prepared.label,
        "failure": {
            "kind": result.failure,
            "pattern": result.pattern,
            "wait_seconds": result.wait_seconds,
            "elapsed_seconds": result.elapsed_seconds,
            "remaining_seconds": result.remaining_seconds,
            "error": result.error,
            "traceback": result.traceback_text,
        },
        "termination": {
            "attempted": result.termination_attempted,
            "succeeded": result.termination_succeeded,
            "error": result.termination_error,
        },
        "matches": [asdict(match) for match in result.matches],
    }
    path = diagnostics_path_for_tail(tail_path)
    path.write_text(f"{json.dumps(diagnostics, indent=2)}\n", encoding="utf-8")


def report_verification_failure(result: VerificationResult) -> None:
    # Every headline shares the trailing "elapsed .../remaining ..." suffix.
    headline = {
        FailureKind.TIMEOUT: lambda: (f"timeout waiting for /{result.pattern}/ "
                                      f"(wait limit {result.wait_seconds:.1f}s, "),
        FailureKind.EOF: lambda: f"EOF before /{result.pattern}/ (",
        FailureKind.FATAL: lambda: f"fatal output /{result.pattern}/ {result.error} (",
        FailureKind.FORBIDDEN: lambda: f"forbidden output /{result.pattern}/ {result.error} (",
        FailureKind.EXCEPTION: lambda: f"verifier exception: {result.error} (",
        FailureKind.INTERRUPTED: lambda: f"verifier exception: {result.error} (",
    }.get(result.failure)
    if headline is not None:
        print(f"\n[demo_runner] FAIL: {headline()}"
              f"elapsed {result.elapsed_seconds:.1f}s, "
              f"remaining {result.remaining_seconds:.1f}s)", file=sys.stderr)

    if result.termination_attempted and not result.termination_succeeded:
        print(f"\n[demo_runner] FAIL: QEMU cleanup: {result.termination_error}",
              file=sys.stderr)


def initialize_repeat_summary(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as summary:
        csv.writer(summary).writerow(("run", "status", "elapsed_seconds", "error"))


def append_repeat_summary(path: Path, attempt: RepeatAttempt) -> None:
    with path.open("a", newline="") as summary:
        csv.writer(summary).writerow((
            attempt.number,
            attempt.status,
            f"{attempt.elapsed_seconds:.3f}",
            attempt.error,
        ))
