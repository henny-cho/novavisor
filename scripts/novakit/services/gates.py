"""The quality gates, and the pinned tool each one needs.

Two consumers ask for these: the `fmt` / `lint` / `test` commands and the CI
lane table. They live below both so a lane never reaches sideways into a
command to find out what a gate is.
"""

from __future__ import annotations

import json
import re
import shutil
import sys

from ..core import config, files, proc
from . import boundaries, cmake

LINT_TREES = ("components", "hal", "nova", "projects")
SOURCE_SUFFIXES = (".c", ".cpp", ".h", ".hpp")


def _clang_format() -> str:
    version = config.tool_version("CLANG_FORMAT_VERSION")
    major = version.split(".", 1)[0]
    for candidate in (f"clang-format-{major}", "clang-format"):
        if shutil.which(candidate, path=config.command_env().get("PATH")) is None:
            continue
        result = proc.run([candidate, "--version"], capture=True, check=False)
        if result.returncode == 0 and f" {major}." in result.stdout:
            return candidate
    raise SystemExit(f"clang-format {major}.x not found (pinned: {version})")


def format_sources(*, check: bool) -> int:
    formatter = _clang_format()
    sources = [str(path) for path in files.tracked(*SOURCE_SUFFIXES)]
    command = [formatter, "--dry-run", "--Werror", *sources] if check else [
        formatter, "-i", *sources
    ]
    result = proc.run(command, capture=check, check=False)
    if result.returncode != 0:
        for stream in (result.stdout, result.stderr):
            if stream:
                print(stream, file=sys.stderr, end="")
    return result.returncode


def _clang_tidy_runner() -> str:
    version = config.tool_version("CLANG_TIDY_VERSION")
    runner = f"run-clang-tidy-{version}"
    if shutil.which(runner, path=config.command_env().get("PATH")) is None:
        raise SystemExit(f"{runner} not found")
    return runner


def lint() -> int:
    spec = cmake.BuildSpec.of(preset="aarch64-debug")
    cmake.build(spec)
    output = cmake.preset_dir(spec.preset)
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

    proc.run(
        [
            _clang_tidy_runner(),
            "-quiet",
            "-p",
            str(output),
            *extra_path.read_text().splitlines(),
            f"-header-filter=/src/({tree_pattern})/",
            source_pattern,
        ]
    )
    return 0


def test() -> int:
    preset = "host-debug"
    proc.run(["cmake", "--preset", preset])
    proc.run(["cmake", "--build", "--preset", preset])
    proc.run(["ctest", "--preset", preset, "--output-on-failure"])
    proc.run(
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


def static_analysis() -> int:
    missing = [
        tool
        for tool in ("ruff", "shellcheck", "actionlint")
        if shutil.which(tool, path=config.command_env().get("PATH")) is None
    ]
    if missing:
        raise SystemExit(
            f"missing static analysis tools: {', '.join(missing)}; run scripts/bootstrap"
        )
    proc.run(["ruff", "check", "--no-cache", "scripts", "tests/scripts"])
    proc.run(["shellcheck", "-x", "--exclude=SC1091", *files.shell_scripts()])
    proc.run(["actionlint"])
    return lint()
