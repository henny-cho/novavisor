"""Repository paths, pinned tool versions, and command environments."""

from __future__ import annotations

import os
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPTS = REPO / "scripts"
BUILD_ROOT = REPO / "build"
DEMO_DIR = REPO / "demo"
DEMO_BUILD_DIR = BUILD_ROOT / "demo"
DEFAULT_CONFIG = REPO / "configs" / "default.yml"
DEFAULT_PAYLOADS = REPO / "configs" / "payloads.yml"
VERSION_SOURCE = SCRIPTS / "tool-versions.env"
HV_PRESET = os.environ.get("NOVA_HV_PRESET", "aarch64-debug")
WEB_DIR = REPO / "web"
WORKBENCH_UI_DIR = WEB_DIR / "workbench"


_ENTRY = re.compile(r"([A-Z][A-Z0-9_]*)=([^\s#]+)")
_SHA256 = re.compile(r"[0-9a-f]{64}")


def tool_versions() -> dict[str, str]:
    """Every pinned version, read the way bash reads the same file.

    The file is sourced by the bootstrap script before any Python exists,
    so it must stay assignments and nothing else. Anything bash would
    execute, or a digest too short to identify an archive, is refused
    here rather than trusted downstream.
    """
    entries: dict[str, str] = {}
    for line in VERSION_SOURCE.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ENTRY.fullmatch(stripped)
        if match is None:
            raise RuntimeError(f"{VERSION_SOURCE}: not a plain assignment: {line!r}")
        name, value = match.groups()
        if "_SHA256_" in name and not _SHA256.fullmatch(value):
            raise RuntimeError(f"{VERSION_SOURCE}: {name} is not a sha256 digest")
        entries[name] = value
    return entries


def tool_version(name: str) -> str:
    override = os.environ.get(name)
    if override:
        return override
    versions = tool_versions()
    if name not in versions:
        raise RuntimeError(f"missing {name} in {VERSION_SOURCE}")
    return versions[name]


def command_env() -> dict[str, str]:
    env = os.environ.copy()
    toolchain = REPO / ".toolchain" / "current" / "bin"
    if toolchain.is_dir():
        path = env.get("PATH", "")
        entries = path.split(os.pathsep)
        if str(toolchain) not in entries:
            env["PATH"] = f"{toolchain}{os.pathsep}{path}"
    env.setdefault("CPM_SOURCE_CACHE", str(REPO / "external" / "cache" / "cpm"))
    return env
