"""Failure reporting: artifact naming, diagnostics files, and console output.

Every filename convention and diagnostics schema a failed run leaves behind is
owned here, so CI can rely on one place defining them.
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from ..core import actions, board, config, proc
from . import artifacts
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
    def from_arg(cls, arg: str | Path | None) -> "ArtifactPaths | None":
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
            "step": {"kind": result.step_kind, "subject": result.step_subject},
            "offender": result.offender,
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
        "steps": [asdict(step) for step in result.results],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{json.dumps(diagnostics, indent=2)}\n", encoding="utf-8")


def report_failure(
    result: VerificationResult,
    *,
    scope: str = "nova demo",
) -> None:
    # Every headline shares the trailing "elapsed .../remaining ..." suffix.
    # `step` says what was owed and `offender` what arrived instead; a
    # headline that named only one of the two left the other unsaid.
    headline = {
        # A step that waited may have learned why it was still waiting;
        # printing the limit without it says only that time ran out.
        FailureKind.TIMEOUT: lambda: (f"timeout waiting for {result.step}"
                                      f"{f' — {result.error}' if result.error else ''} "
                                      f"(wait limit {result.wait_seconds:.1f}s, "),
        FailureKind.EOF: lambda: f"EOF before {result.step} (",
        FailureKind.FATAL: lambda: f"fatal output /{result.offender}/ "
                                   f"while waiting for {result.step} (",
        FailureKind.FORBIDDEN: lambda: f"forbidden output /{result.offender}/ "
                                       f"{result.error or f'while waiting for {result.step}'} (",
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
    the CSV.
    """
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
    actions.step_summary(*lines)


def _first_line(cmd: list[str]) -> str:
    try:
        out = proc.run(cmd, capture=True, check=False).stdout
    except OSError as exc:
        return f"unavailable: {exc}"
    return out.splitlines()[0] if out else "unavailable"


class Evidence:
    """A directory of copied artifacts plus what identifies the machine.

    A workflow uploads this directory as-is instead of hard-coding build-tree
    paths that silently rot when the harness moves.
    """

    def __init__(self, artifacts: ArtifactPaths):
        self.root = artifacts.root / "evidence"
        self.root.mkdir(parents=True, exist_ok=True)
        self.collected: list[Path] = []

    def keep(self, src: Path, rename: str | None = None) -> None:
        if src.is_file():
            dest = self.root / (rename or src.name)
            shutil.copy2(src, dest)
            self.collected.append(dest)

    def keep_image(self, elf: Path, rename: str | None = None) -> None:
        """An image, meaning the ELF and whatever answers for it.

        What that includes is `artifacts.copy_image`'s to decide; this
        only registers what it produced. Restating the policy here is
        how a snapshot and an evidence bundle come to hold different
        ideas of what an image is.
        """
        if elf.is_file():
            self.collected.extend(
                artifacts.copy_image(elf, self.root / (rename or elf.name)))

    def keep_preset(self, preset: str) -> None:
        """The inputs and output of one build preset, tagged with its name."""
        preset_dir = config.BUILD_ROOT / preset
        self.keep_image(preset_dir / "novavisor.elf", f"{preset}-novavisor.elf")
        for name in ("active_config.yml", "active_payloads.yml"):
            self.keep(preset_dir / name, f"{preset}-{name}")
        for dtb in sorted((preset_dir / "guest_dtb").glob("*.dtb")):
            self.keep(dtb, f"{preset}-{dtb.name}")

    def finish(self) -> None:
        (self.root / "environment.txt").write_text("\n".join((
            _first_line(["git", "-C", str(config.REPO), "rev-parse", "HEAD"]),
            _first_line([board.QEMU, "--version"]),
            _first_line(["aarch64-none-elf-gcc", "--version"]),
        )) + "\n", encoding="utf-8")
        (self.root / "sha256sums.txt").write_text("".join(
            f"{hashlib.sha256(item.read_bytes()).hexdigest()}  {item.name}\n"
            for item in self.collected
        ), encoding="utf-8")


def collect_evidence(
    artifacts: ArtifactPaths,
    name: str,
    manifest: dict,
    elf_snapshots: list[Path],
) -> None:
    """Everything one demo's failure investigation needs, next to its tails."""
    evidence = Evidence(artifacts)
    for index, snapshot in enumerate(elf_snapshots, start=1):
        evidence.keep_image(snapshot, f"variant-{index}-novavisor.elf")
    preset_dir = config.BUILD_ROOT / config.HV_PRESET
    evidence.keep(preset_dir / "active_config.yml")
    evidence.keep(preset_dir / "active_payloads.yml")
    for dtb in sorted((preset_dir / "guest_dtb").glob("*.dtb")):
        evidence.keep(dtb)
    guest_cache = config.REPO / "external" / "cache" / "guests" / name
    for guest in manifest.get("guests", []):
        binary = Path(guest["binary"])
        evidence.keep(config.DEMO_BUILD_DIR / name / binary.name)
        evidence.keep(guest_cache / binary.name)
        evidence.keep(guest_cache / f"{binary.stem}.elf")
    if guest_cache.is_dir():
        for stamp in sorted(guest_cache.glob("*.version")):
            evidence.keep(stamp)
    evidence.finish()


def collect_lane_evidence(artifacts: ArtifactPaths, presets: tuple[str, ...]) -> None:
    """Everything a failed CI lane needs explained, for whichever step failed.

    The lane cannot know which artifact matters, so it keeps the build state
    of every preset it drove plus the images and firmware log it produced.
    """
    evidence = Evidence(artifacts)
    for preset in presets:
        evidence.keep_preset(preset)
    firmware = config.BUILD_ROOT / "qemu-tfa-firmware"
    evidence.keep(firmware / "smoke.log")
    for diagnostics in sorted(firmware.glob("*.json")):
        evidence.keep(diagnostics)
    if config.DEMO_BUILD_DIR.is_dir():
        for image in sorted(config.DEMO_BUILD_DIR.rglob("*.bin")):
            evidence.keep(image, f"{image.parent.name}-{image.name}")
    evidence.finish()
