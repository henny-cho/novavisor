"""Formatting, static analysis, tests, and local gates."""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

from . import build, config, process


LINT_TREES = ("components", "hal", "nova", "projects")
SOURCE_SUFFIXES = {".c", ".cpp", ".h", ".hpp"}
EXCLUDED_PARTS = {".git", ".toolchain", "build", "external"}


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


def _check_format_pin() -> None:
    version = config.tool_version("CLANG_FORMAT_VERSION")
    hooks = (config.REPO / ".pre-commit-config.yaml").read_text()
    if f"rev: v{version}" not in hooks:
        raise SystemExit(f"clang-format hook does not match {version}")


def format_sources(*, check: bool) -> int:
    formatter = _clang_format()
    files = [str(path) for path in source_files()]
    if check:
        _check_format_pin()
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
    process.run([sys.executable, str(config.REPO / "tools" / "check_platform_boundaries.py")])
    return 0
