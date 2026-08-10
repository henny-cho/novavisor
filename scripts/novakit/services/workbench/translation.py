"""Address translation: descriptor encodings, and the walk over them.

Bit layouts are read from the headers the hypervisor compiles rather
than restated here, so a field that moves moves here too and a header
this cannot parse stops the tool instead of decoding into nonsense.

Two encodings: a guest's Stage 2 (IPA to PA) puts the memory type in the
descriptor, while a Stage 1 regime indexes it through a MAIR and carries
a single privilege level's AP field. A guest's own Stage 1 shares that
encoding and differs in geometry, which it takes from its own TCR_EL1,
and in where its tables live: guest IPA space, so every table read is
itself a Stage 2 translation. `GuestReader` is that second walk, put
where the hardware puts it.
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from dataclasses import dataclass, replace

from ...core import config
from ...image import abi

_CORE_MMU = (
    config.REPO / "src" / "components" / "core" / "core_mmu" / "include" / "core_mmu"
)
STAGE2_DESCRIPTOR = _CORE_MMU / "stage2_descriptor.hpp"
STAGE2_BUILDER = _CORE_MMU / "stage2_builder.hpp"
STAGE1_TABLES = config.REPO / "src" / "hal" / "arch" / "aarch64" / "vmsa" / "stage1_tables.hpp"
STE_MODEL = (
    config.REPO / "src" / "components" / "device" / "smmu" / "include" / "smmu" / "ste_model.hpp"
)

_S2 = abi.read_constexprs(STAGE2_DESCRIPTOR)
_S2.update(abi.read_constexprs(STAGE2_BUILDER, _S2))
_S1 = abi.read_constexprs(STAGE1_TABLES)
# Which table set a device stream walks. Its geometry fields come from
# the Stage 2 definition, so that dictionary seeds the read.
STE = abi.read_constexprs(STE_MODEL, _S2)

# A descriptor is one 64-bit word at every level of both regimes.
DESCRIPTOR_BYTES = 8
# What TBI takes out of an input address: the architecture's top byte.
_TAG_BITS = 8
_ADDRESS_BITS = 64
# The level whose entries are one granule; coarser levels count down.
_GRANULE_LEVEL = 3
# ARM ARM DDI0487 §D8.2.6: for a 4 KiB granule VTCR_EL2.SL0 names the
# level a Stage 2 walk starts at. Here to hold T0SZ and SL0 — two
# declarations of one geometry — against each other.
_SL0_START_LEVEL = {0b00: 2, 0b01: 1, 0b10: 0, 0b11: 3}


@dataclass(frozen=True)
class Geometry:
    """How a walk descends.

    `shifts` is where each level's index field starts in the input
    address, coarsest first; `levels` is what the architecture calls
    those levels. Both are derived: a level number from how far its
    index sits above the granule, the starting level from the input
    width.

    `fill` is what the bits above `address_bits` must be for the address
    to belong to this regime — zero for a low half or a Stage 2 input,
    all ones for a high half, where the rule is a pattern rather than a
    magnitude. `tagged` drops the top byte first, which is what a regime
    with TBI set tells the hardware to do.
    """

    name: str
    shifts: tuple[int, ...]
    levels: tuple[int, ...]
    entries: int
    address_bits: int
    fill: int = 0
    tagged: bool = False

    @property
    def top(self) -> int:
        """How many bits of the input this regime reads at all."""
        return _ADDRESS_BITS - (_TAG_BITS if self.tagged else 0)

    def holds(self, address: int) -> bool:
        """Is this address in this regime's half?"""
        above = (address & ((1 << self.top) - 1)) >> self.address_bits
        return above == (((1 << (self.top - self.address_bits)) - 1) if self.fill else 0)

    def within(self, address: int) -> int:
        """The part of the address a walk indexes with."""
        return address & ((1 << self.address_bits) - 1)

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
    starts_at: int | None = None,
    fill: int = 0,
    tagged: bool = False,
) -> Geometry:
    """Build one regime's geometry, checking its declarations agree.

    The finest level indexes the granule, so its shift is the granule
    and the coarser levels count down from there.
    """
    index_bits = entries.bit_length() - 1
    if 1 << index_bits != entries:
        raise SystemExit(f"nova workbench: {name} has {entries} entries per table, not a power of two")
    levels = tuple(_GRANULE_LEVEL - (shift - shifts[-1]) // index_bits for shift in shifts)
    # The top level's index has to reach the top of the input address and
    # no further; one bit either way starts the walk at a different level
    # than the tables were built for.
    if not shifts[0] < address_bits <= shifts[0] + index_bits:
        raise SystemExit(
            f"nova workbench: {name} indexes from bit {shifts[0]} but its input is {address_bits} bits"
        )
    if starts_at is not None and starts_at != levels[0]:
        raise SystemExit(
            f"nova workbench: {name} is configured to walk from L{starts_at}, "
            f"but a {address_bits}-bit input starts at L{levels[0]}"
        )
    built = Geometry(name, shifts, levels, entries, address_bits, fill, tagged)
    # The bits above the input are what says which half an address is in,
    # so a regime that reads none of them can answer neither half.
    if built.top <= address_bits:
        raise SystemExit(f"nova workbench: {name} reads {built.top} bits but indexes {address_bits} of them")
    return built


STAGE2 = _geometry(
    "stage2",
    (_S2["kL1Shift"], _S2["kL2Shift"], _S2["kL3Shift"]),
    _S2["kTableEntries"],
    64 - _S2["kStage2T0sz"],
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
)


# TCR_EL1.TG0 and TG1 encode the same three granules differently, so
# each half reads its own table. ARM ARM DDI0487 §D8.2.9.
_TG0_GRANULE = {0b00: 12, 0b01: 16, 0b10: 14}
_TG1_GRANULE = {0b01: 14, 0b10: 12, 0b11: 16}


def guest_geometry(tcr: int, high: bool) -> Geometry:
    """One half of a guest's Stage 1, from the register that defines it.

    A guest picks its own granule and input width while EL2's are build
    constants, so this is the same construction over a live value rather
    than a second kind of geometry. What half it is decides where every
    field sits and what the bits above the input must be.

    Whether the half exists at all is not answered here: EPD disables
    one, SCTLR_EL1.M disables both, and an unpublished bank means the
    regime is unknown — three different things that a single "no
    geometry" would flatten into one.
    """
    table = _TG1_GRANULE if high else _TG0_GRANULE
    field = (tcr >> (30 if high else 14)) & 0b11
    if field not in table:
        # A guest writes TCR_EL1, so a reserved granule is a thing that
        # happens rather than a build fault. Named, because the caller
        # turns it into "this regime cannot be walked".
        raise ValueError(f"TCR_EL1.TG{int(high)} = {field:#04b} is reserved")
    granule = table[field]
    address_bits = _ADDRESS_BITS - (((tcr >> 16) if high else tcr) & 0b111111)
    index_bits = granule - 3
    return _geometry(
        f"guest-stage1-{'high' if high else 'low'}",
        tuple(range(granule, address_bits, index_bits))[::-1],
        1 << index_bits,
        address_bits,
        fill=1 if high else 0,
        tagged=bool(tcr & (1 << (38 if high else 37))),  # TBI1 / TBI0
    )


def guest_half_enabled(tcr: int, high: bool) -> bool:
    """Has the guest left this half translating? TCR_EL1.EPD1 / EPD0."""
    return not tcr & (1 << (23 if high else 7))


@dataclass(frozen=True)
class Descriptor:
    """One table slot as the hardware reads it.

    `output` is the next table for a table descriptor and the mapped
    address for a leaf. The permission fields mean anything only at a
    leaf, where the walk ends and the access is decided.
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
    # S2AP is a two-bit read/write pair; the header's "write only" value
    # is the write bit, so its position stays the header's business.
    s2ap = (raw & _S2["kS2apMask"]) >> _S2["kS2apShift"]
    attr = (raw & _S2["kMemAttrMask"]) >> _S2["kMemAttrShift"]
    return {
        "writable": bool(s2ap & _S2["kS2apWriteOnly"]),
        "executable": not raw & _S2["kXnBit"],
        "accessed": bool(raw & _S2["kAfBit"]),
        "memory": _S2_MEMORY.get(attr, f"memattr:{attr:#x}"),
    }


def _stage1_attributes(raw: int) -> dict[str, object]:
    # A single-privilege regime: AP[1] is RES1 and only AP[2] carries
    # meaning, so read-only is the whole of the permission.
    attr = (raw & _S1["kAttrIndxMask"]) >> _S1["kAttrIndxShift"]
    return {
        "writable": not raw & _S1["kApReadOnly"],
        "executable": not raw & _S1["kXnBit"],
        "accessed": bool(raw & _S1["kAfBit"]),
        "memory": _S1_MEMORY.get(attr, f"mair:{attr}"),
    }


@dataclass(frozen=True)
class Format:
    """One regime's descriptor encoding, and its walk geometry.

    `wxn` is whether the regime's control register forbids writable and
    executable together — read from the firmware, where EL2 sets
    SCTLR_EL2.WXN and a guest's Stage 2 grants both on purpose.
    """

    geometry: Geometry
    type_mask: int
    type_invalid: int
    type_block: int
    output_mask: int
    attributes: Callable[[int], dict[str, object]]
    wxn: bool = False

    def decode(self, raw: int, depth: int) -> Descriptor:
        """Read one slot of the table at `depth`.

        The depth separates a table descriptor from a page: both are the
        same two bits and only the level says which the hardware reads.
        By the same rule a block encoding at the last level is reserved,
        so it decodes as invalid.

        A leaf's output is aligned to what it maps — the bits below are
        ignored by the hardware — so the tree and a probe cannot report
        the address differently.
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
        return Descriptor(
            raw, kind, output & ~(self.geometry.span(depth) - 1), **self.attributes(raw)
        )


def _format(constants: dict[str, int], geometry: Geometry, attributes, wxn: bool = False) -> Format:
    """One regime's Format, from the dictionary its header was read into.

    Both headers name these fields the same way, so the two regimes are
    one construction over two dictionaries.
    """
    return Format(
        geometry=geometry,
        type_mask=constants["kTypeMask"],
        type_invalid=constants["kTypeInvalid"],
        type_block=constants["kTypeBlock"],
        output_mask=constants["kOutputAddrMask"],
        attributes=attributes,
        wxn=wxn,
    )


STAGE2_FORMAT = _format(_S2, STAGE2, _stage2_attributes)
STAGE1_FORMAT = _format(
    _S1, STAGE1, _stage1_attributes, wxn=bool(_S1["kSctlrEl2"] & _S1["kSctlrWxn"])
)


class Stage2Fault(ValueError):
    """A guest table read the guest's Stage 2 refused.

    Its own type because the two failures a walk can meet here are fixed
    in different places: an address the guest never mapped is the guest's
    business, and a guest table the host never mapped is ours. A reader
    error alone cannot tell them apart, and the architecture does not —
    it reports S1PTW beside the fault.
    """

    def __init__(self, ipa: int, found: "Probe"):
        super().__init__(f"stage-2 {found.fault or 'fault'} at L{found.level} for {ipa:#x}")


@dataclass(frozen=True)
class GuestReader:
    """A guest's IPA space, read through the guest's own Stage 2.

    The walk takes a reader and a reader answers `read_bytes`, so a
    regime whose tables live one translation further down needs nothing
    else: this is what the hardware does to every level of a guest's
    Stage 1 walk, in the shape the walk already accepts.
    """

    reader: object
    root: int

    def read_bytes(self, ipa: int, size: int) -> bytes:
        found = probe(self.reader, STAGE2_FORMAT, self.root, ipa)
        if found.output is None:
            raise Stage2Fault(ipa, found)
        return self.reader.read_bytes(found.output, size)


# --- The walk ---------------------------------------------------------------
#
# Pure functions over a `MemoryReader` and a `Format`. The bytes come
# from mapped RAM or from a recorded copy and nothing here knows which,
# which is what lets a replay answer a question it never recorded.


@dataclass(frozen=True)
class Step:
    """One level of a probe: the table read, the slot, what was in it."""

    depth: int
    index: int
    table: int
    descriptor: Descriptor


@dataclass(frozen=True)
class Probe:
    """Where one address lands.

    `output` is the translated address when the walk completed and
    `fault` says why it did not otherwise. `level` is where it ended
    either way — the number a translation fault reports in ESR_EL2.
    """

    address: int
    steps: tuple[Step, ...]
    level: int
    output: int | None = None
    fault: str = ""


@dataclass(frozen=True)
class Node:
    """A slot of a table, or a run of slots that say the same thing.

    `map_range` lays a region down as consecutive entries differing only
    in output address, so a mapped window arrives as hundreds of
    identical descriptors; one node with a count is what the builder
    wrote. Table descriptors never fold — each points somewhere else.
    """

    depth: int
    index: int
    count: int
    base: int
    descriptor: Descriptor
    children: tuple[Node, ...] = ()


@dataclass(frozen=True)
class Tree:
    """Every mapping under one root.

    Invalid slots are absent: they are most of every table, and their
    count is the table size less what is here. `read` counts the tables
    fetched and `unreadable` names those that could not be — without
    which a short recording reads as a machine with fewer mappings.
    """

    root: int
    nodes: tuple[Node, ...]
    read: int
    unreadable: tuple[int, ...] = ()
    truncated: bool = False


class _Walk:
    """One traversal, and the tables it is allowed to read.

    The limit is the pool the firmware built these tables from: wanting
    more means following a word that is not a table pointer, which
    unbounded costs the reader a gigabyte of reads.
    """

    def __init__(self, reader, fmt: Format, limit: int):
        self.reader = reader
        self.fmt = fmt
        self.limit = limit
        self.read = 0
        self.unreadable: list[int] = []
        self.truncated = False

    def table(self, pa: int) -> tuple[int, ...] | None:
        if self.read >= self.limit:
            self.truncated = True
            return None
        self.read += 1
        geometry = self.fmt.geometry
        try:
            raw = self.reader.read_bytes(pa, geometry.table_bytes)
        except ValueError:
            # Whichever layer refused it: a tree names the table rather
            # than the reason, and `read` says how far it got.
            self.unreadable.append(pa)
            return None
        return struct.unpack(f"<{geometry.entries}Q", raw)


def probe(reader, fmt: Format, root: int, address: int) -> Probe:
    """Follow one address down, the way the hardware would.

    One descriptor per level rather than a table: a probe is one path,
    and the rest of each table is not on it.
    """
    geometry = fmt.geometry
    if not geometry.holds(address):
        return Probe(address, (), geometry.levels[0], fault="address-size")
    inside = geometry.within(address)
    steps: list[Step] = []
    table = root
    for depth in range(geometry.depth):
        index = geometry.index(inside, depth)
        try:
            raw = reader.read_bytes(table + index * DESCRIPTOR_BYTES, DESCRIPTOR_BYTES)
        except Stage2Fault:
            # This regime's table is itself an address that needs
            # translating, and that translation is the one that failed.
            return Probe(address, tuple(steps), geometry.levels[depth], fault="s1ptw")
        except ValueError:
            return Probe(address, tuple(steps), geometry.levels[depth], fault="unreadable")
        descriptor = fmt.decode(int.from_bytes(raw, "little"), depth)
        steps.append(Step(depth, index, table, descriptor))
        if descriptor.kind == "invalid":
            return Probe(address, tuple(steps), geometry.levels[depth], fault="translation")
        if descriptor.maps:
            # The output is already aligned to what it maps; the offset
            # into it comes from the address being translated.
            offset = inside & (geometry.span(depth) - 1)
            return Probe(
                address, tuple(steps), geometry.levels[depth], output=descriptor.output | offset
            )
        table = descriptor.output
    return Probe(address, tuple(steps), geometry.levels[-1], fault="translation")


def tree(reader, fmt: Format, root: int, limit: int) -> Tree:
    """Everything one root maps, folded into runs."""
    walk = _Walk(reader, fmt, limit)
    nodes = _descend(walk, root, 0, 0)
    return Tree(root, nodes, walk.read, tuple(walk.unreadable), walk.truncated)


def _descend(walk: _Walk, table: int, depth: int, base: int) -> tuple[Node, ...]:
    words = walk.table(table)
    if words is None:
        return ()
    span = walk.fmt.geometry.span(depth)
    nodes: list[Node] = []
    for index, raw in enumerate(words):
        descriptor = walk.fmt.decode(raw, depth)
        if descriptor.kind == "invalid":
            continue
        at = base + index * span
        if descriptor.kind == "table":
            under = _descend(walk, descriptor.output, depth + 1, at)
            nodes.append(Node(depth, index, 1, at, descriptor, under))
        elif nodes and _extends(nodes[-1], descriptor, index, span, walk.fmt.output_mask):
            nodes[-1] = replace(nodes[-1], count=nodes[-1].count + 1)
        else:
            nodes.append(Node(depth, index, 1, at, descriptor))
    return tuple(nodes)


def _extends(node: Node, descriptor: Descriptor, index: int, span: int, output_mask: int) -> bool:
    """Is this slot the next step of the run `node` already holds?

    Everything outside the output address field has to match, decoded or
    not: a hint this does not read is still a difference, and folding
    across it would report a region the builder never wrote.
    """
    return (
        node.descriptor.kind == descriptor.kind
        and node.index + node.count == index
        and node.descriptor.output + node.count * span == descriptor.output
        and node.descriptor.raw & ~output_mask == descriptor.raw & ~output_mask
    )
