"""Single source of demo-harness configuration.

Every path, preset, and board flag the harness relies on lives here, so a
change has exactly one place to land. NOVA_* environment variables override
the tool selections without editing code.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEMO_DIR = REPO / "demo"
BUILD_DIR = REPO / "build"
DEMO_BUILD_DIR = BUILD_DIR / "demo"
HV_PRESET = os.environ.get("NOVA_HV_PRESET", "aarch64-debug")
QEMU = os.environ.get("NOVA_QEMU", "qemu-system-aarch64")
# Single source of truth for the QEMU board model. scripts/task.sh run/debug
# consume it via the `qemu-args` subcommand instead of copying the flags.
QEMU_BOARD_ARGS = (
    "-machine", "virt,virtualization=on,gic-version=3,iommu=smmuv3,highmem-ecam=off",
    "-cpu", "cortex-a57",
    "-smp", "2",  # must match NOVA_BOARD_SMP_CPUS (board_layout.h)
    "-nographic",
    "-nic", "none",
    "-m", "1024",
)
FATAL_OUTPUT_PATTERNS = (
    r"\[smmu\] initialization failed(?::| error=)",
    r"\[smmu\] isolation failure:",
)
# NOVA_GUEST_IPA_BASE (nova/abi/guest_layout.h): every guest links here.
GUEST_LINK_BASE = 0x50000000


def hv_elf() -> Path:
    """Hypervisor ELF of the active preset, resolved on every call so a
    redirected BUILD_DIR or HV_PRESET takes effect."""
    return BUILD_DIR / HV_PRESET / "novavisor.elf"
