"""Repository paths, pinned tool versions, and command environments."""

from __future__ import annotations

import os
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
BUILD_ROOT = REPO / "build"
DEMO_DIR = REPO / "demo"
DEMO_BUILD_DIR = BUILD_ROOT / "demo"
DEFAULT_CONFIG = REPO / "configs" / "default.yml"
DEFAULT_PAYLOADS = REPO / "configs" / "payloads.yml"
VERSION_SOURCE = SCRIPTS / "tool-versions.env"
HV_PRESET = os.environ.get("NOVA_HV_PRESET", "aarch64-debug")
QEMU = os.environ.get("NOVA_QEMU", "qemu-system-aarch64")
QEMU_BOARD_ARGS = (
    "-machine",
    "virt,virtualization=on,gic-version=3,iommu=smmuv3,highmem-ecam=off",
    "-cpu",
    "cortex-a57",
    "-smp",
    "2",
    "-nographic",
    "-nic",
    "none",
    "-m",
    "1024",
)
FATAL_OUTPUT_PATTERNS = (
    r"\[smmu\] initialization failed(?::| error=)",
    r"\[smmu\] isolation failure:",
)
GUEST_LINK_BASE = 0x50000000


def tool_version(name: str) -> str:
    override = os.environ.get(name)
    if override:
        return override
    match = re.search(
        rf"^{re.escape(name)}=([^\s#]+)$",
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
    env.setdefault("CPM_SOURCE_CACHE", str(REPO / "external" / "cache" / "cpm"))
    return env
