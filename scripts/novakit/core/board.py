"""The QEMU board every run targets, and the output that always means failure.

One owner for the machine model: a demo, a firmware chain and a soak must
disagree about nothing except the image they boot.
"""

from __future__ import annotations

import os
from pathlib import Path

QEMU = os.environ.get("NOVA_QEMU", "qemu-system-aarch64")
MACHINE_ARGS = (
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

# Output that invalidates a run no matter what else was expected: the
# isolation the demos assert is already gone by the time it prints.
FATAL_PATTERNS = (
    r"\[smmu\] initialization failed(?::| error=)",
    r"\[smmu\] isolation failure:",
)


def command(
    *,
    kernel: Path | None = None,
    bios: Path | None = None,
    secure: bool = False,
) -> list[str]:
    args = list(MACHINE_ARGS)
    if secure:
        args[1] = f"{args[1]},secure=on"
    argv = [QEMU, *args]
    if kernel is not None:
        argv += ["-kernel", str(kernel)]
    if bios is not None:
        argv += ["-bios", str(bios)]
    return argv
