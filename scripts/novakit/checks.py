"""Formatting, static analysis, tests, and local gates."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from collections.abc import Callable
from pathlib import Path

from . import build, config, demo, firmware, manifest, process, report
from .services import boundaries

LINT_TREES = ("components", "hal", "nova", "projects")
SOURCE_SUFFIXES = {".c", ".cpp", ".h", ".hpp"}
EXCLUDED_PARTS = {".git", ".toolchain", "build", "external"}
CI_LANES = ("host", "static", "runtime")


def source_files() -> list[Path]:
    return sorted(
        path
        for path in config.REPO.rglob("*")
        if path.is_file()
        and path.suffix in SOURCE_SUFFIXES
        and EXCLUDED_PARTS.isdisjoint(path.relative_to(config.REPO).parts)
    )


def _clang_format() -> str:
    version = config.tool_version("CLANG_FORMAT_VERSION")
    major = version.split(".", 1)[0]
    for candidate in (f"clang-format-{major}", "clang-format"):
        if shutil.which(candidate, path=config.command_env().get("PATH")) is None:
            continue
        result = process.run(
            [candidate, "--version"],
            capture=True,
            check=False,
        )
        if result.returncode == 0 and f" {major}." in result.stdout:
            return candidate
    raise SystemExit(f"clang-format {major}.x not found (pinned: {version})")


def format_sources(*, check: bool) -> int:
    formatter = _clang_format()
    files = [str(path) for path in source_files()]
    if check:
        command = [formatter, "--dry-run", "--Werror", *files]
    else:
        command = [formatter, "-i", *files]
    result = process.run(command, capture=check, check=False)
    if result.returncode != 0:
        if result.stdout:
            print(result.stdout, file=sys.stderr, end="")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
    return result.returncode


def _clang_tidy_runner() -> str:
    version = config.tool_version("CLANG_TIDY_VERSION")
    runner = f"run-clang-tidy-{version}"
    if shutil.which(runner, path=config.command_env().get("PATH")) is None:
        raise SystemExit(f"{runner} not found")
    return runner


def lint() -> int:
    spec = build.BuildSpec("aarch64-debug")
    build.build(spec)
    output = build.preset_dir(spec.preset)
    extra_path = output / "clang_tidy_extra_args.txt"
    compile_path = output / "compile_commands.json"
    if not extra_path.is_file() or not compile_path.is_file():
        raise SystemExit("clang-tidy build metadata is missing")

    for tree in LINT_TREES:
        if not (config.REPO / "src" / tree).is_dir():
            raise SystemExit(f"lint scope does not exist: src/{tree}")

    tree_pattern = "|".join(LINT_TREES)
    source_pattern = rf"^{re.escape(str(config.REPO))}/src/({tree_pattern})/.*\.cpp$"
    entries = json.loads(compile_path.read_text())
    selected = {entry["file"] for entry in entries if re.match(source_pattern, entry["file"])}
    if not selected:
        raise SystemExit("clang-tidy selected 0 source files")
    print(f"clang-tidy selected {len(selected)} translation units", file=sys.stderr)

    extra_args = extra_path.read_text().splitlines()
    process.run(
        [
            _clang_tidy_runner(),
            "-quiet",
            "-p",
            str(output),
            *extra_args,
            f"-header-filter=/src/({tree_pattern})/",
            source_pattern,
        ]
    )
    return 0


def test() -> int:
    preset = "host-debug"
    process.run(["cmake", "--preset", preset])
    process.run(["cmake", "--build", "--preset", preset])
    process.run(["ctest", "--preset", preset, "--output-on-failure"])
    process.run(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            str(config.REPO / "tests" / "scripts"),
            "-p",
            "*_test.py",
        ]
    )
    if boundaries.check(config.REPO) != 0:
        raise SystemExit(1)
    return 0


def _shell_files() -> list[str]:
    paths = []
    for path in config.REPO.rglob("*"):
        if (
            not path.is_file()
            or EXCLUDED_PARTS.intersection(path.relative_to(config.REPO).parts)
        ):
            continue
        first_line = path.read_text(errors="ignore").splitlines()[:1]
        if path.suffix == ".sh" or (
            first_line and re.match(r"^#!.*\b(?:ba|z)?sh\b", first_line[0])
        ):
            paths.append(str(path.relative_to(config.REPO)))
    return sorted(paths)


def static_checks() -> int:
    missing = [
        tool
        for tool in ("ruff", "shellcheck", "actionlint")
        if shutil.which(tool, path=config.command_env().get("PATH")) is None
    ]
    if missing:
        raise SystemExit(
            f"missing static analysis tools: {', '.join(missing)}; run scripts/bootstrap"
        )
    process.run(["ruff", "check", "--no-cache", "scripts", "tests/scripts"])
    process.run(["shellcheck", "-x", "--exclude=SC1091", *_shell_files()])
    process.run(["actionlint"])
    return lint()


def runtime_checks() -> int:
    for preset in (
        "aarch64-release",
        "aarch64-minimal-release",
        "aarch64-standard-release",
    ):
        build.build(build.BuildSpec(preset))

    firmware.build_profile("n1sdp")
    if firmware.verify_qemu_tfa(build_only=False) != 0:
        return 1
    if demo.fetch_all() != 0:
        return 1

    artifacts = report.ArtifactPaths(config.BUILD_ROOT / "demo-failures")
    artifacts.initialize()
    if demo.verify_all(artifacts) != 0:
        return 1
    for demo_id in range(7, 11):
        name = manifest.resolve_demo(str(demo_id))
        if demo.verify(name, artifacts, preset="aarch64-standard-release") != 0:
            return 1
    return 0


def _append_ci_summary(
    lane: str,
    steps: list[tuple[str, str, float]],
) -> None:
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
    stats = process.run([ccache, "--show-stats"], capture=True, check=False)
    if stats.returncode == 0:
        with Path(summary).open("a", encoding="utf-8") as output:
            output.write("\n<details><summary>ccache</summary>\n\n```text\n")
            output.write(stats.stdout)
            output.write("```\n</details>\n")


def ci(lane: str) -> int:
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
            ("format", lambda: format_sources(check=True)),
            ("tests", test),
        ),
        "static": (("static-analysis", static_checks),),
        "runtime": (("runtime-verification", runtime_checks),),
    }
    selected = CI_LANES if lane == "all" else (lane,)
    try:
        for selected_lane in selected:
            for name, action in handlers[selected_lane]:
                run_step(f"{selected_lane}/{name}", action)
    finally:
        _append_ci_summary(lane, steps)
    return 0
