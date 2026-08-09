"""CI lanes: ownership, execution order, timing, and failure evidence."""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass

from ..core import actions, config, proc
from . import cmake, gates, report, suite, tfa
from .workbench import checks

RUNTIME_PRESETS = (
    "aarch64-release",
    "aarch64-minimal-release",
    "aarch64-standard-release",
)
RECHECK_PRESET = "aarch64-standard-release"
EVIDENCE_PRESETS = (config.HV_PRESET, RECHECK_PRESET, "aarch64-n1sdp-release")
RUNTIME_RECHECK = (
    "07_lifecycle",
    "08_smp_pingpong",
    "09_guest_smp",
    "10_console_mux",
)
EVIDENCE = report.ArtifactPaths(config.BUILD_ROOT / "ci-evidence")


@dataclass(frozen=True)
class Lane:
    name: str
    steps: tuple[tuple[str, Callable[[], int]], ...]

    def __post_init__(self) -> None:
        # Step names key the job summary, so a repeat would report two
        # outcomes under one heading. Refuse the table, not the report.
        names = [step for step, _ in self.steps]
        if len(names) != len(set(names)):
            raise ValueError(f"lane {self.name}: duplicate step name")


# Keep these adapters late-bound so tests and callers can replace a service
# operation without rebuilding the immutable lane table.
def _format() -> int:
    return gates.format_sources(check=True)


def _tests() -> int:
    return gates.test()


def _static() -> int:
    return gates.static_analysis()


def _manifest() -> int:
    # Runs after static-analysis, whose lint pass built the debug ELF.
    return checks.verify_manifest()


def _presets() -> int:
    for preset in RUNTIME_PRESETS:
        cmake.build(cmake.BuildSpec.of(preset=preset))
    return 0


def _firmware_chain() -> int:
    tfa.build_profile("n1sdp")
    return tfa.verify_chain(build_only=False)


def _demos() -> int:
    if suite.fetch_all() != 0:
        return 1
    return suite.verify_all(EVIDENCE)


def _recheck() -> int:
    for name in RUNTIME_RECHECK:
        if suite.verify_one(name, EVIDENCE, preset=RECHECK_PRESET) != 0:
            return 1
    return 0


LANES = (
    Lane("host", (("format", _format), ("tests", _tests))),
    Lane("static", (("static-analysis", _static), ("manifest", _manifest))),
    Lane(
        "runtime",
        (
            ("presets", _presets),
            ("firmware", _firmware_chain),
            ("demos", _demos),
            ("recheck", _recheck),
        ),
    ),
)
BY_NAME = {lane.name: lane for lane in LANES}


def _ccache_stats() -> tuple[str, ...]:
    """Return the summary's compiler-cache block when ccache is available."""
    ccache = shutil.which("ccache", path=config.command_env().get("PATH"))
    if ccache is None:
        return ()
    stats = proc.run([ccache, "--show-stats"], capture=True, check=False)
    if stats.returncode != 0:
        return ()
    return (
        "<details><summary>ccache</summary>",
        "",
        "```text",
        stats.stdout.rstrip("\n"),
        "```",
        "</details>",
    )


def _append_summary(lane: str, steps: list[tuple[str, str, float]]) -> None:
    actions.step_summary(
        f"## nova ci {lane}",
        "",
        "| Step | Result | Seconds |",
        "| --- | --- | ---: |",
        *(f"| {name} | {status} | {elapsed:.1f} |" for name, status, elapsed in steps),
        "",
        *_ccache_stats(),
    )


def run_lane(lane: str) -> int:
    """Run one lane (or every lane), timing each step and reporting the table."""
    steps: list[tuple[str, str, float]] = []
    EVIDENCE.initialize()

    def run_step(name: str, action: Callable[[], int]) -> None:
        started = time.monotonic()
        status = "pass"
        try:
            if action() != 0:
                raise SystemExit(f"CI step failed: {name}")
        except BaseException:
            status = "fail"
            raise
        finally:
            steps.append((name, status, time.monotonic() - started))

    selected = LANES if lane == "all" else (BY_NAME[lane],)
    try:
        for entry in selected:
            for name, action in entry.steps:
                run_step(f"{entry.name}/{name}", action)
    finally:
        if any(status == "fail" for _, status, _ in steps):
            report.collect_lane_evidence(EVIDENCE, EVIDENCE_PRESETS)
        _append_summary(lane, steps)
    return 0
