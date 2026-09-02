"""Use cases for the primary hypervisor workspace."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..core import board, proc
from . import artifacts, cmake
from .workbench import checks

INSPECTORS: dict[str, Callable[[Path], list[str]]] = {
    "size": lambda elf: ["aarch64-none-elf-size", str(elf)],
    "disassemble": lambda elf: [
        "aarch64-none-elf-objdump",
        "-d",
        "-S",
        "-C",
        str(elf),
    ],
}


def build(spec: cmake.BuildSpec) -> int:
    cmake.build(spec)
    return 0


def clean() -> int:
    cmake.clean()
    return 0


def run(spec: cmake.BuildSpec, *, debug: bool = False) -> int:
    elf = cmake.resolve_elf(spec, rebuild=True)
    command = board.command(kernel=elf, cpu_model=artifacts.runtime_cpu(elf))
    if debug:
        command += ["-s", "-S"]
        print("==> Launching QEMU with GDB stub on :1234 (CPU halted).")
        print("==> Press Ctrl-A then x in QEMU to exit.")
    return proc.call(command)


def inspect(spec: cmake.BuildSpec, operation: str) -> int:
    elf = cmake.resolve_elf(spec, rebuild=False)
    return proc.call(INSPECTORS[operation](elf))


def inspect_symbols(spec: cmake.BuildSpec) -> int:
    """In-process, unlike INSPECTORS: the reader is our own manifest."""
    return checks.describe_symbols(cmake.resolve_elf(spec, rebuild=False))
