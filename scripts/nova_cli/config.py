"""Repository paths, pinned tool versions, and command environments."""

from __future__ import annotations

import os
import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
BUILD_ROOT = REPO / "build"
DEFAULT_CONFIG = REPO / "configs" / "default.yml"
DEFAULT_PAYLOADS = REPO / "configs" / "payloads.yml"
VERSION_SOURCE = SCRIPTS / "lib" / "versions.sh"


def tool_version(name: str) -> str:
    override = os.environ.get(name)
    if override:
        return override
    match = re.search(
        rf'^export {re.escape(name)}="([^"]+)"$',
        VERSION_SOURCE.read_text(),
        flags=re.MULTILINE,
    )
    if match is None:
        raise RuntimeError(f"missing {name} in {VERSION_SOURCE}")
    return match.group(1)


def command_env() -> dict[str, str]:
    env = os.environ.copy()
    toolchain = REPO / ".toolchain" / "current" / "bin"
    if toolchain.is_dir():
        path = env.get("PATH", "")
        entries = path.split(os.pathsep)
        if str(toolchain) not in entries:
            env["PATH"] = f"{toolchain}{os.pathsep}{path}"
    env.setdefault("CPM_SOURCE_CACHE", str(REPO / "external" / "cache"))
    return env
