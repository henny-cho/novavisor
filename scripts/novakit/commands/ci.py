"""CI lanes: the steps each lane owns, timed and reported the same way."""

from __future__ import annotations

import os
import shutil
import time
from collections.abc import Callable
from pathlib import Path

from ..core import config, proc
from ..services import cmake, manifest, report, tfa
from . import check, demo, firmware

RUNTIME_PRESETS = (
    "aarch64-release",
    "aarch64-minimal-release",
    "aarch64-standard-release",
)
LANES = ("host", "static", "runtime")


def runtime_checks() -> int:
    for preset in RUNTIME_PRESETS:
        cmake.build(cmake.BuildSpec.of(preset=preset))

    tfa.build_profile("n1sdp")
    if firmware.verify_chain(build_only=False) != 0:
        return 1
    if demo.fetch_all() != 0:
        return 1

    artifacts = report.ArtifactPaths(config.BUILD_ROOT / "demo-failures")
    artifacts.initialize()
    if demo.verify_all(artifacts) != 0:
        return 1
    for demo_id in range(7, 11):
        name = manifest.resolve_demo(str(demo_id))
        if demo.verify_one(name, artifacts, preset="aarch64-standard-release") != 0:
            return 1
    return 0


def _append_summary(lane: str, steps: list[tuple[str, str, float]]) -> None:
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary:
        return
    lines = [
        f"## nova ci {lane}",
        "",
        "| Step | Result | Seconds |",
        "| --- | --- | ---: |",
        *(f"| {name} | {status} | {elapsed:.1f} |" for name, status, elapsed in steps),
        "",
    ]
    with Path(summary).open("a", encoding="utf-8") as output:
        output.write("\n".join(lines))

    ccache = shutil.which("ccache", path=config.command_env().get("PATH"))
    if ccache is None:
        return
    stats = proc.run([ccache, "--show-stats"], capture=True, check=False)
    if stats.returncode == 0:
        with Path(summary).open("a", encoding="utf-8") as output:
            output.write("\n<details><summary>ccache</summary>\n\n```text\n")
            output.write(stats.stdout)
            output.write("```\n</details>\n")


def run_lane(lane: str) -> int:
    steps: list[tuple[str, str, float]] = []

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

    handlers = {
        "host": (
            ("format", lambda: check.format_sources(check=True)),
            ("tests", check.test),
        ),
        "static": (("static-analysis", check.static_analysis),),
        "runtime": (("runtime-verification", runtime_checks),),
    }
    selected = LANES if lane == "all" else (lane,)
    try:
        for selected_lane in selected:
            for name, action in handlers[selected_lane]:
                run_step(f"{selected_lane}/{name}", action)
    finally:
        _append_summary(lane, steps)
    return 0


def _ci(args) -> int:
    return run_lane(args.lane)


def register(subcommands) -> None:
    parser = subcommands.add_parser("ci", help="run a CI lane locally")
    parser.add_argument("lane", choices=(*LANES, "all"))
    parser.set_defaults(handler=_ci)
