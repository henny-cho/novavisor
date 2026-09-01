"""One subprocess boundary for automation commands."""

from __future__ import annotations

import shlex
import subprocess  # noqa: TID251 — this is the process boundary
import sys
from collections.abc import Sequence
from pathlib import Path

from . import config


def log(argv: Sequence[str]) -> None:
    visible = argv[:12]
    suffix = f" ... +{len(argv) - len(visible)} args" if len(argv) > len(visible) else ""
    print(f"$ {shlex.join(visible)}{suffix}", file=sys.stderr)


def run(
    argv: Sequence[str],
    *,
    cwd: Path = config.REPO,
    capture: bool = False,
    check: bool = True,
    timeout: float | None = None,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [str(arg) for arg in argv]
    log(command)
    return subprocess.run(
        command,
        cwd=cwd,
        env=config.command_env(),
        check=check,
        text=True,
        capture_output=capture,
        timeout=timeout,
        input=stdin,
    )


def call(argv: Sequence[str], *, cwd: Path = config.REPO) -> int:
    command = [str(arg) for arg in argv]
    log(command)
    return subprocess.call(command, cwd=cwd, env=config.command_env())
