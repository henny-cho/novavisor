"""Address translation, read from the headers that encode it.

A descriptor's bit layout is the firmware's fact and it is written down
once, in the headers the hypervisor itself compiles. This module reads
those headers instead of restating them, so a field that moves moves
here too and a header this cannot parse stops the tool rather than
decoding into plausible nonsense.

Two regimes, two encodings. A guest's Stage 2 (IPA to PA) is EL2's own
table with the memory type encoded directly in the descriptor; EL2's
Stage 1 (its identity map of itself) indexes attributes through
MAIR_EL2 and carries a single privilege level's AP field. Nothing here
touches a guest's own Stage 1: those tables live in guest RAM and change
under the guest's hand, where every table this module reads is built
once at boot and never rewritten.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ...core import config
from ...image import abi

_CORE_MMU = (
    config.REPO / "src" / "components" / "core" / "core_mmu" / "include" / "core_mmu"
)
STAGE2_DESCRIPTOR = _CORE_MMU / "stage2_descriptor.hpp"
STAGE2_BUILDER = _CORE_MMU / "stage2_builder.hpp"
STAGE1_TABLES = config.REPO / "src" / "hal" / "arch" / "aarch64" / "vmsa" / "stage1_tables.hpp"

_S2 = abi.read_constexprs(STAGE2_DESCRIPTOR)
_S2.update(abi.read_constexprs(STAGE2_BUILDER, _S2))
_S1 = abi.read_constexprs(STAGE1_TABLES)

# A descriptor is one 64-bit word at every level of both regimes.
DESCRIPTOR_BYTES = 8

# ARM ARM DDI0487 §D8.2.6: for a 4 KiB granule VTCR_EL2.SL0 names the
# level a Stage 2 walk starts at. An architectural table, not a repo
# fact — it is here to check the header's two declarations against each
# other, since T0SZ and SL0 have to agree and nothing else says so.
_SL0_START_LEVEL = {0b00: 2, 0b01: 1, 0b10: 0, 0b11: 3}


@dataclass(frozen=True)
class Geometry:
    """How a walk descends.

    `shifts` is where each level's index field starts in the input
    address, coarsest first, and `levels` is what the architecture calls
    those levels. Both are derived rather than declared: a level number
    follows from how far its index sits above the granule, and which
    level a walk starts at follows from how wide the input address is.
    """

    name: str
    shifts: tuple[int, ...]
    levels: tuple[int, ...]
    entries: int
    address_bits: int

    @property
    def table_bytes(self) -> int:
        return self.entries * DESCRIPTOR_BYTES

    @property
    def depth(self) -> int:
        return len(self.shifts)

    def index(self, address: int, depth: int) -> int:
        """Which slot of the table at `depth` this address selects."""
        return (address >> self.shifts[depth]) & (self.entries - 1)

    def span(self, depth: int) -> int:
        """How much address space one entry at `depth` covers."""
        return 1 << self.shifts[depth]


def _geometry(
    name: str,
    shifts: tuple[int, ...],
    entries: int,
    address_bits: int,
    granule_shift: int,
    starts_at: int | None = None,
) -> Geometry:
    index_bits = entries.bit_length() - 1
    if 1 << index_bits != entries:
        raise SystemExit(f"nova workbench: {name} has {entries} entries per table, not a power of two")
    levels = tuple(3 - (shift - granule_shift) // index_bits for shift in shifts)
    # The top level's index field has to reach the top of the input
    # address and no further: one bit either way and the walk starts at a
    # different level than the tables were built for.
    if not shifts[0] < address_bits <= shifts[0] + index_bits:
        raise SystemExit(
            f"nova workbench: {name} indexes from bit {shifts[0]} but its input is {address_bits} bits"
        )
    if starts_at is not None and starts_at != levels[0]:
        raise SystemExit(
            f"nova workbench: {name} declares a walk from L{starts_at}, "
            f"its address width starts one at L{levels[0]}"
        )
    return Geometry(name, shifts, levels, entries, address_bits)


STAGE2 = _geometry(
    "stage2",
    (_S2["kL1Shift"], _S2["kL2Shift"], _S2["kL3Shift"]),
    _S2["kTableEntries"],
    64 - _S2["kStage2T0sz"],
    _S2["kL3Shift"],
    starts_at=_SL0_START_LEVEL[_S2["kStage2Sl0"]],
)

# The EL2 builder names its levels by what one entry covers rather than
# by where the index sits, so the shifts come back out of the sizes.
STAGE1 = _geometry(
    "el2-stage1",
    tuple(
        block.bit_length() - 1 for block in (_S1["kBlockL1"], _S1["kBlockL2"], _S1["kPageSize"])
    ),
    _S1["kEntries"],
    _S1["kVaLimit"].bit_length() - 1,
    _S1["kPageSize"].bit_length() - 1,
)


@dataclass(frozen=True)
class Descriptor:
    """One table slot as the hardware reads it.

    `output` is the next table's address for a table descriptor and the
    mapped output address for a leaf. The permission fields carry meaning
    only at a leaf, where a walk ends and an access is actually decided.
    """

    raw: int
    kind: str  # invalid | table | block | page
    output: int = 0
    writable: bool = False
    executable: bool = False
    accessed: bool = False
    memory: str = ""

    @property
    def maps(self) -> bool:
        """Does this descriptor end a walk with an output address?"""
        return self.kind in ("block", "page")


_S2_MEMORY = {
    _S2["kMemAttrNormalWB"]: "normal-wb",
    _S2["kMemAttrDeviceNGnRE"]: "device-nGnRE",
}
_S1_MEMORY = {
    _S1["kAttrIndxDevice"]: "device-nGnRE",
    _S1["kAttrIndxNormal"]: "normal-wb",
}


def _stage2_attributes(raw: int) -> dict[str, object]:
    # S2AP is a two-bit read/write pair, and the header names the value
    # that is write alone — which is the write bit, without this file
    # having to know where in the field it sits.
    s2ap = (raw & _S2["kS2apMask"]) >> _S2["kS2apShift"]
    attr = (raw & _S2["kMemAttrMask"]) >> _S2["kMemAttrShift"]
    return {
        "writable": bool(s2ap & _S2["kS2apWriteOnly"]),
        "executable": not raw & _S2["kXnBit"],
        "accessed": bool(raw & _S2["kAfBit"]),
        "memory": _S2_MEMORY.get(attr, f"memattr:{attr:#x}"),
    }


def _stage1_attributes(raw: int) -> dict[str, object]:
    # A single-privilege regime: AP[1] is RES1 and only AP[2] means
    # anything, so read-only is the whole of the permission.
    attr = (raw & _S1["kAttrIndxMask"]) >> _S1["kAttrIndxShift"]
    return {
        "writable": not raw & _S1["kApReadOnly"],
        "executable": not raw & _S1["kXnBit"],
        "accessed": bool(raw & _S1["kAfBit"]),
        "memory": _S1_MEMORY.get(attr, f"mair:{attr}"),
    }


@dataclass(frozen=True)
class Format:
    """One regime's descriptor encoding, and its walk geometry."""

    geometry: Geometry
    type_mask: int
    type_invalid: int
    type_block: int
    output_mask: int
    attributes: Callable[[int], dict[str, object]]

    def decode(self, raw: int, depth: int) -> Descriptor:
        """Read one slot of the table at `depth`.

        The depth is what separates a table descriptor from a page: both
        are the same two bits, and only the level says which one the
        hardware is looking at. The same rule makes a block descriptor at
        the last level nothing at all — the architecture reserves that
        encoding there, so it is reported as invalid rather than as a
        mapping the hardware would never make.
        """
        leaf = depth == self.geometry.depth - 1
        bits = raw & self.type_mask
        if bits == self.type_invalid:
            return Descriptor(raw, "invalid")
        if bits == self.type_block:
            if leaf:
                return Descriptor(raw, "invalid")
            kind = "block"
        else:
            kind = "page" if leaf else "table"
        output = raw & self.output_mask
        if kind == "table":
            return Descriptor(raw, kind, output)
        return Descriptor(raw, kind, output, **self.attributes(raw))


STAGE2_FORMAT = Format(
    geometry=STAGE2,
    type_mask=_S2["kTypeMask"],
    type_invalid=_S2["kTypeInvalid"],
    type_block=_S2["kTypeBlock"],
    output_mask=_S2["kOutputAddrMask"],
    attributes=_stage2_attributes,
)

STAGE1_FORMAT = Format(
    geometry=STAGE1,
    type_mask=_S1["kTypeMask"],
    type_invalid=_S1["kTypeInvalid"],
    type_block=_S1["kTypeBlock"],
    output_mask=_S1["kOutputAddrMask"],
    attributes=_stage1_attributes,
)
