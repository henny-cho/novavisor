"""Guest ABI limits, read from the headers that define them.

The platform headers keep their constants as plain #defines so the
assembler, the linker script, the C/C++ compilers and this reader all see
one definition. Everything that accepts a guest specification — the demo
manifest loader and the DTB generator — validates through here, so a
configuration the hypervisor cannot honour is rejected the same way
whichever entry point reads it first.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
GUEST_LAYOUT = REPO / "src" / "nova" / "abi" / "guest_layout.h"


def read_defines(path: Path, wanted: list[str]) -> dict[str, int]:
    """Pull integer #define constants from a platform header."""
    text = path.read_text()
    values: dict[str, int] = {}
    for name in wanted:
        match = re.search(
            rf"^#define\s+{re.escape(name)}\s+(0[xX][0-9a-fA-F]+|\d+)",
            text,
            re.MULTILINE,
        )
        if match is None:
            raise ValueError(f"{name} not found in {path}")
        values[name] = int(match.group(1), 0)
    return values


def read_define(path: Path, name: str) -> int:
    return read_defines(path, [name])[name]


def read_string_define(path: Path, name: str) -> str:
    match = re.search(
        rf'^#define\s+{re.escape(name)}\s+"([^"]+)"',
        path.read_text(),
        re.MULTILINE,
    )
    if match is None:
        raise ValueError(f"{name} not found in {path}")
    return match.group(1)


LIMITS = read_defines(GUEST_LAYOUT, ["NOVA_MAX_GUESTS", "NOVA_MAX_VCPUS_PER_VM"])
MAX_GUESTS = LIMITS["NOVA_MAX_GUESTS"]
MAX_VCPUS_PER_VM = LIMITS["NOVA_MAX_VCPUS_PER_VM"]
UART_KINDS = ("none", "vuart")  # UartKind (nova/abi/guest.hpp)


def validate_guest(where: str, spec: dict) -> int:
    """Reject a guest spec the ABI cannot honour; return its vCPU count.

    `where` names the offending source for the diagnostic (a manifest and
    a guest config reach this from different directions).
    """
    requested = spec.get("vcpus", 1)
    try:
        vcpus = int(requested)
    except (TypeError, ValueError):
        sys.exit(f"{where}: vcpus must be an integer (got {requested!r})")
    if not 1 <= vcpus <= MAX_VCPUS_PER_VM:
        sys.exit(f"{where}: vcpus {vcpus} (supported: 1..{MAX_VCPUS_PER_VM})")

    uart = spec.get("uart", "none")
    if uart not in UART_KINDS:
        sys.exit(f"{where}: uart '{uart}' (supported: {', '.join(UART_KINDS)})")
    return vcpus
