"""The translation regimes a run has, and the tables behind them.

Every table named here is built once during EL2 init and never rewritten
— the same fact that lets `nova_stage2_switch` retarget VTTBR without
invalidating a TLB. One copy per run is therefore the whole of them
rather than a sample, so the copy rides out on the topology and the
walker reads it instead of RAM. A client joining late, and a replay with
no RAM behind it at all, then get the bytes the machine actually had.

A guest's own Stage 1 is not here and cannot be: those tables live in
guest RAM under the guest's hand, where nothing above holds.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import elfsym, translation

# Where the tables live. Extents come from the DWARF, so a resized pool
# is copied whole without this list changing.
TABLES = (
    "nova::(anonymous)::g_stage2_sets",
    "nova::smmu::(anonymous)::g_dma_tables",
    "nova_el2_l1_root",
    "(anonymous)::g_pool",
)
# Where each walk starts, as the machine itself holds it: the register
# value the CPU is given, and the root the SMMU's stream table is built
# from. Read from the plan instead, these would describe a run that was
# intended rather than one that happened.
ROOTS = (
    "nova::(anonymous)::g_vttbr",
    "nova::smmu::(anonymous)::g_contexts",
    "nova::smmu::(anonymous)::g_context_count",
)
SYMBOLS = TABLES + ROOTS

# EL2's Stage 1 root is its own table plus the pool the builder draws
# from; both are in TABLES, and the walk may reach every one of them.
_EL2_ROOT = "nova_el2_l1_root"
_EL2_POOL = "(anonymous)::g_pool"


def _field(info: elfsym.TypeInfo, name: str) -> elfsym.Field:
    for member in info.fields:
        if member.name == name:
            return member
    raise KeyError(f"{info.name or info.kind} has no field {name!r}")


def _word(reader, pa: int) -> int:
    return int.from_bytes(reader.read_bytes(pa, translation.DESCRIPTOR_BYTES), "little")


def _tables_in(pool_bytes: int, geometry: translation.Geometry) -> int:
    """How many tables a walk from this root may read.

    The pool the firmware built them from, which is what makes a walk
    wanting more a walk following something that is not a table.
    """
    return pool_bytes // geometry.table_bytes


def _stage2_regimes(reader, resolved: dict[str, elfsym.ResolvedSymbol]) -> list[dict]:
    vttbr = resolved["nova::(anonymous)::g_vttbr"]
    sets = resolved["nova::(anonymous)::g_stage2_sets"]
    budget = _tables_in(sets.type.element.size, translation.STAGE2)
    regimes = []
    for vm in range(vttbr.type.count):
        # Zero is a guest whose tables were never built, not a root at
        # address zero: the array is cleared and filled per live guest.
        value = _word(reader, vttbr.address + vm * vttbr.type.element.size)
        if value == 0:
            continue
        regimes.append(
            {
                "id": f"vm{vm}.cpu",
                "label": f"VM {vm} · CPU",
                "role": "cpu",
                "vm": vm,
                "kind": "stage2",
                "root": f"{value & translation.STAGE2_FORMAT.output_mask:#x}",
                "tables": budget,
            }
        )
    return regimes


def _dma_regimes(reader, resolved: dict[str, elfsym.ResolvedSymbol]) -> list[dict]:
    contexts = resolved["nova::smmu::(anonymous)::g_contexts"]
    count_at = resolved["nova::smmu::(anonymous)::g_context_count"]
    tables = resolved["nova::smmu::(anonymous)::g_dma_tables"]
    budget = _tables_in(tables.type.element.size, translation.STAGE2)
    entry = contexts.type.element
    owner = _field(entry, "owner_vm")
    root = _field(entry, "root_pa")
    regimes = []
    count = min(_word(reader, count_at.address), contexts.type.count)
    for index in range(count):
        at = contexts.address + index * entry.size
        vm = int.from_bytes(reader.read_bytes(at + owner.offset, owner.type.size), "little")
        regimes.append(
            {
                "id": f"vm{vm}.dma",
                "label": f"VM {vm} · DMA",
                "role": "dma",
                "vm": vm,
                "kind": "stage2",
                "root": f"{_word(reader, at + root.offset):#x}",
                "tables": budget,
            }
        )
    return regimes


def _el2_regime(resolved: dict[str, elfsym.ResolvedSymbol]) -> dict:
    root = resolved[_EL2_ROOT]
    pool = resolved[_EL2_POOL]
    return {
        "id": "el2",
        "label": "EL2 · self",
        "role": "self",
        "vm": None,
        "kind": "stage1",
        "root": f"{root.address:#x}",
        "tables": _tables_in(root.size + pool.size, translation.STAGE1),
    }


def capture(reader, resolved: dict[str, elfsym.ResolvedSymbol]) -> dict | None:
    """Read every root and every table once, as the wire carries them.

    Returns None until EL2 has built the tables. The RAM backend exists
    from the moment QEMU starts, so an early read would copy a page of
    zeros and publish it as the machine's whole address map.

    Only the words that are set travel. An empty slot is the invalid
    descriptor, the large majority of every table here, and the extents
    say where the zeros were — so what arrives is the same bytes, not a
    smaller version of them.

    A reader with no image behind it resolves nothing and has no map;
    that is a capability, not a failure.
    """
    if not resolved:
        return None
    regimes = _stage2_regimes(reader, resolved)
    if not regimes:
        return None
    words = {}
    extents = []
    for symbol in TABLES:
        entry = resolved[symbol]
        extents.append([f"{entry.address:#x}", entry.size])
        raw = reader.read_bytes(entry.address, entry.size)
        for offset in range(0, entry.size, translation.DESCRIPTOR_BYTES):
            value = int.from_bytes(raw[offset : offset + translation.DESCRIPTOR_BYTES], "little")
            if value:
                words[f"{entry.address + offset:#x}"] = f"{value:#x}"
    return {
        "regimes": [*regimes, *_dma_regimes(reader, resolved), _el2_regime(resolved)],
        "extents": extents,
        "words": words,
    }


@dataclass(frozen=True)
class Tables:
    """A `MemoryReader` over a captured copy.

    The walker's only source, live and in replay alike. One reader means
    a replay cannot answer differently from the run it recorded, where
    two would agree only for as long as nobody changed one of them.
    """

    words: dict[int, int]
    extents: tuple[tuple[int, int], ...]

    @classmethod
    def of(cls, captured: dict) -> Tables:
        return cls(
            {int(at, 16): int(word, 16) for at, word in captured["words"].items()},
            tuple((int(at, 16), size) for at, size in captured["extents"]),
        )

    def read_bytes(self, pa: int, size: int) -> bytes:
        """Bytes from the copy, or nothing at all.

        A read outside what was captured fails rather than returning
        zeros: zeros are invalid descriptors, and a walk given them would
        call an address unmapped when the truth is that this copy never
        held it. Reads are descriptor-aligned because that is all a table
        is made of, and one that is not would land between the words this
        holds and come back empty.
        """
        stride = translation.DESCRIPTOR_BYTES
        held = any(base <= pa and pa + size <= base + span for base, span in self.extents)
        if size < 0 or pa % stride or size % stride or not held:
            raise ValueError(f"{pa:#x}+{size:#x} is outside the captured tables")
        out = bytearray(size)
        for offset in range(0, size, stride):
            word = self.words.get(pa + offset)
            if word is not None:
                out[offset : offset + stride] = word.to_bytes(stride, "little")
        return bytes(out)


# --- Answering a client -----------------------------------------------------

FORMATS = {"stage2": translation.STAGE2_FORMAT, "stage1": translation.STAGE1_FORMAT}


def _address(value) -> int:
    """A probe target, from whatever the client typed.

    Hex because these are addresses and a reader has them in hex; the
    prefix is optional because a reader pasting one from a fault message
    has it either way.
    """
    try:
        # Always hex, prefix or not. Reading a bare string as decimal
        # would answer a different question than the one asked, and the
        # answer would look perfectly ordinary.
        return int(str(value).strip().replace("_", ""), 16)
    except ValueError:
        raise ValueError(f"{value!r} is not an address") from None


def answer(captured: dict, request: dict) -> dict:
    """Walk one regime for a client.

    Live and in replay this reads the same captured tables, so the two
    cannot answer differently — the walk is not reimplemented on either
    side of a recording.
    """
    wanted = str(request.get("regime", ""))
    regime = next((entry for entry in captured["regimes"] if entry["id"] == wanted), None)
    if regime is None:
        raise KeyError(f"no regime {wanted!r}")
    fmt = FORMATS[regime["kind"]]
    reader = Tables.of(captured)
    root = int(regime["root"], 16)
    data = {
        "regime": regime["id"],
        "tree": _tree_wire(translation.tree(reader, fmt, root, regime["tables"]), fmt),
    }
    if request.get("address") not in (None, ""):
        found = translation.probe(reader, fmt, root, _address(request["address"]))
        data["probe"] = _probe_wire(found, fmt)
    return data


def _wx_slots(nodes: tuple[translation.Node, ...]) -> int:
    """Slots this map makes both writable and executable.

    A count rather than a verdict. Whether it is a defect depends on the
    regime, and the regime says so: EL2's control register forbids the
    combination where a guest's Stage 2 grants it deliberately.
    """
    total = 0
    for node in nodes:
        if node.descriptor.writable and node.descriptor.executable:
            total += node.count
        total += _wx_slots(node.children)
    return total


def _tree_wire(found: translation.Tree, fmt: translation.Format) -> dict:
    return {
        "root": f"{found.root:#x}",
        "tables": found.tables,
        "truncated": found.truncated,
        "unreadable": [f"{pa:#x}" for pa in found.unreadable],
        "wx": _wx_slots(found.nodes),
        "wxn": fmt.wxn,
        "nodes": [_node_wire(node, fmt) for node in found.nodes],
    }


def _node_wire(node: translation.Node, fmt: translation.Format) -> dict:
    """One row of the map.

    Carries the span it covers rather than the level's shift: a client
    computing that would be holding a second copy of the geometry, and
    the whole point of reading it from the headers is that there is one.
    """
    descriptor = node.descriptor
    wire = {
        "level": fmt.geometry.levels[node.depth],
        "index": node.index,
        "count": node.count,
        "base": f"{node.base:#x}",
        "size": f"{node.count * fmt.geometry.span(node.depth):#x}",
        "kind": descriptor.kind,
        "output": f"{descriptor.output:#x}",
    }
    if descriptor.maps:
        # Only where a walk ends. A table descriptor given permissions
        # would be showing bits the hardware does not consult.
        wire |= {
            "w": descriptor.writable,
            "x": descriptor.executable,
            "af": descriptor.accessed,
            "memory": descriptor.memory,
        }
    if node.children:
        wire["children"] = [_node_wire(child, fmt) for child in node.children]
    return wire


def _probe_wire(found: translation.Probe, fmt: translation.Format) -> dict:
    # Where it landed and what may be done there: half of what an address
    # means is the permission the walk ended on, so the answer carries it
    # rather than sending the reader back into the tree to look.
    leaf = found.steps[-1].descriptor if found.steps else None
    wire = {
        "address": f"{found.address:#x}",
        "level": found.level,
        "fault": found.fault,
        "output": None if found.output is None else f"{found.output:#x}",
        "steps": [
            {
                "level": fmt.geometry.levels[step.depth],
                "index": step.index,
                "table": f"{step.table:#x}",
                "kind": step.descriptor.kind,
            }
            for step in found.steps
        ],
    }
    if found.output is not None and leaf is not None:
        wire |= {"w": leaf.writable, "x": leaf.executable, "memory": leaf.memory}
    return wire
