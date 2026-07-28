"""CI lanes: what each lane owns, declared once and timed the same way."""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass

from ..core import actions, config, proc
from ..services import cmake, gates, report, suite, tfa

# Profiles the runtime lane links, in the order it links them.
RUNTIME_PRESETS = (
    "aarch64-release",
    "aarch64-minimal-release",
    "aarch64-standard-release",
)
RECHECK_PRESET = "aarch64-standard-release"
# Presets whose build state explains a runtime failure: the one the demos run,
# the one they are re-verified against, and the one the firmware chain links.
EVIDENCE_PRESETS = (config.HV_PRESET, RECHECK_PRESET, "aarch64-n1sdp-release")
# Demos re-run against the standard profile: lifecycle, SMP, guest SMP, and
# console multiplexing all depend on the component set that profile adds.
RUNTIME_RECHECK = (
    "07_lifecycle",
    "08_smp_pingpong",
    "09_guest_smp",
    "10_console_mux",
)
# One directory per lane run: failure tails, diagnostics, and the build state
# a workflow uploads without naming a single build-tree path.
EVIDENCE = report.ArtifactPaths(config.BUILD_ROOT / "ci-evidence")


@dataclass(frozen=True)
class Lane:
    name: str
    steps: tuple[tuple[str, Callable[[], int]], ...]


def _format() -> int:
    return gates.format_sources(check=True)


def _tests() -> int:
    return gates.test()


def _static() -> int:
    return gates.static_analysis()


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
    Lane("static", (("static-analysis", _static),)),
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
    """The compiler-cache block of the summary, empty when ccache is absent."""
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


def _ci(args) -> int:
    return run_lane(args.lane)


def register(subcommands) -> None:
    parser = subcommands.add_parser("ci", help="run a CI lane locally")
    parser.add_argument("lane", choices=(*BY_NAME, "all"))
    parser.set_defaults(handler=_ci)
