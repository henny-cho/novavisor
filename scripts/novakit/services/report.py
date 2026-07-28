"""Failure reporting: artifact naming, diagnostics files, and console output.

Every filename convention and diagnostics schema a failed run leaves behind is
owned here, so CI can rely on one place defining them.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from ..core import board, config, proc
from .expect import FailureKind, RepeatAttempt, VerificationResult


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


def write_diagnostics(
    path: Path,
    label: str,
    result: VerificationResult,
) -> None:
    diagnostics = {
        "label": label,
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{json.dumps(diagnostics, indent=2)}\n", encoding="utf-8")


def report_failure(
    result: VerificationResult,
    *,
    scope: str = "nova demo",
) -> None:
    # Every headline shares the trailing "elapsed .../remaining ..." suffix.
    headline = {
        FailureKind.TIMEOUT: lambda: (f"timeout waiting for /{result.pattern}/ "
                                      f"(wait limit {result.wait_seconds:.1f}s, "),
        FailureKind.EOF: lambda: f"EOF before /{result.pattern}/ (",
        FailureKind.FATAL: lambda: f"fatal output /{result.pattern}/ {result.error} (",
        FailureKind.FORBIDDEN: lambda: f"forbidden output /{result.pattern}/ {result.error} (",
        FailureKind.EXCEPTION: lambda: f"verifier exception: {result.error} (",
        FailureKind.INTERRUPTED: lambda: f"verifier exception: {result.error} (",
        FailureKind.SPAWN: lambda: f"QEMU spawn: {result.error} (",
    }.get(result.failure)
    if headline is not None:
        print(f"\n[{scope}] FAIL: {headline()}"
              f"elapsed {result.elapsed_seconds:.1f}s, "
              f"remaining {result.remaining_seconds:.1f}s)", file=sys.stderr)

    if result.termination_attempted and not result.termination_succeeded:
        print(f"\n[{scope}] FAIL: QEMU cleanup: {result.termination_error}",
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


def append_github_summary(
    title: str,
    attempts: list[RepeatAttempt],
    summary_csv: Path | None,
) -> None:
    """Publish the soak result to the GitHub Actions step summary.

    The harness already knows the pass rate, so workflows never post-process
    the CSV. No-op outside Actions (GITHUB_STEP_SUMMARY unset).
    """
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        return
    passed = sum(1 for attempt in attempts if attempt.ok)
    total = len(attempts)
    rate = 100.0 * passed / total if total else 0.0
    elapsed = sum(attempt.elapsed_seconds for attempt in attempts)
    lines = [
        f"## {title}",
        "",
        f"**Result:** {passed}/{total} passed ({rate:.1f}%), total {elapsed:.1f}s",
        "",
    ]
    if summary_csv is not None and summary_csv.exists():
        lines += ["```csv", summary_csv.read_text().rstrip("\n"), "```"]
    with open(target, "a", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")


def _first_line(cmd: list[str]) -> str:
    try:
        out = proc.run(cmd, capture=True, check=False).stdout
    except OSError as exc:
        return f"unavailable: {exc}"
    return out.splitlines()[0] if out else "unavailable"


def collect_evidence(
    artifacts: ArtifactPaths,
    name: str,
    manifest: dict,
    elf_snapshots: list[Path],
) -> None:
    """Copy everything a failure investigation needs next to the QEMU tails.

    A workflow then uploads the artifacts directory as-is instead of
    hard-coding build-tree paths that silently rot when the harness moves.
    """
    evidence = artifacts.root / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    collected: list[Path] = []

    def keep(src: Path, rename: str | None = None) -> None:
        if src.is_file():
            dest = evidence / (rename or src.name)
            shutil.copy2(src, dest)
            collected.append(dest)

    for index, snapshot in enumerate(elf_snapshots, start=1):
        keep(snapshot, f"variant-{index}-novavisor.elf")
    preset_dir = config.BUILD_ROOT / config.HV_PRESET
    keep(preset_dir / "active_config.yml")
    keep(preset_dir / "active_payloads.yml")
    for dtb in sorted((preset_dir / "guest_dtb").glob("*.dtb")):
        keep(dtb)
    guest_cache = config.REPO / "external" / "cache" / "guests" / name
    for guest in manifest.get("guests", []):
        binary = Path(guest["binary"])
        keep(config.DEMO_BUILD_DIR / name / binary.name)
        keep(guest_cache / binary.name)
        keep(guest_cache / f"{binary.stem}.elf")
    if guest_cache.is_dir():
        for stamp in sorted(guest_cache.glob("*.version")):
            keep(stamp)

    (evidence / "environment.txt").write_text("\n".join((
        _first_line(["git", "-C", str(config.REPO), "rev-parse", "HEAD"]),
        _first_line([board.QEMU, "--version"]),
        _first_line(["aarch64-none-elf-gcc", "--version"]),
    )) + "\n", encoding="utf-8")
    (evidence / "sha256sums.txt").write_text("".join(
        f"{hashlib.sha256(item.read_bytes()).hexdigest()}  {item.name}\n"
        for item in collected
    ), encoding="utf-8")
