"""The QEMU board every run targets.

One owner for the machine model: a demo, a firmware chain and a soak must
disagree about nothing except the image they boot.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Sequence
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


def aperture_bytes(command: Sequence[str]) -> int:
    """How much guest RAM a composed command asks for, from its own -m.

    Read back rather than passed alongside: an observation backend is a
    file of exactly this size, and whoever places that file needs the
    number the machine will actually be given.
    """
    return int(command[list(command).index("-m") + 1]) << 20


def attach_workbench(
    command: list[str],
    *,
    shm_path: Path | str,
    qmp_path: Path | str,
    gdb_path: Path | str | None = None,
) -> list[str]:
    """Extend a composed command with the workbench's observation surfaces.

    Guest RAM becomes a shareable file the bridge can mmap read-only, and
    a QMP socket exposes machine-level control. The frozen MACHINE_ARGS
    stay untouched: the memory size is read back from the command's own
    -m value, and the backend is merged into its -machine string.

    Refused when the filesystem holding that file cannot fit it. QEMU
    allocates the backend lazily, so a short one does not fail at launch:
    it fails when the guest touches the page that does not fit, and the
    machine dies with no output and nothing saying why. A container's
    default /dev/shm is 64 MiB, which holds a small guest and not a
    Linux one.
    """
    argv = list(command)
    memory_mib = argv[argv.index("-m") + 1]
    need = aperture_bytes(argv)
    free = shutil.disk_usage(Path(shm_path).parent).free
    if free < need:
        raise SystemExit(
            f"[board] guest RAM backend needs {need >> 20} MiB under "
            f"{Path(shm_path).parent}, which has {free >> 20} MiB free"
        )
    argv[argv.index("-machine") + 1] += ",memory-backend=wbram"
    argv += [
        "-object",
        f"memory-backend-file,id=wbram,size={memory_mib}M,mem-path={shm_path},share=on",
        "-qmp",
        f"unix:{qmp_path},server=on,wait=off",
    ]
    if gdb_path is not None:
        argv += ["-gdb", f"unix:{gdb_path},server=on,wait=off"]
    return argv
