"""The translation regimes a run has, and the tables behind them.

Two questions, answered separately because their answers change at
different times. The roster — which regimes exist, where each is rooted,
what encoding it uses — is read every poll: a guest turns its own MMU on
long after EL2 built its tables. The copy is read once, and only of the
regimes whose ground says it can be: those tables are written during EL2
init and never again, the same fact that lets `nova_stage2_switch`
retarget VTTBR without invalidating a TLB.

A regime says which of the two it is. The copy rides the topology and
the walker reads it instead of RAM, which is how a late joiner and a
replay with no RAM get the same bytes; a regime whose ground moves has
no copy to ride, so its answer is a reading taken at a moment.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...image import abi, elfsym, observe
from . import translation

# vCPU slots are flat: a VM owns a fixed stride of them, so a slot names
# its VM by division rather than by a table this file would keep.
_VCPUS_PER_VM = abi.MAX_VCPUS_PER_VM

# Descriptor formats by the name the wire calls their kind, so the two
# cannot drift.
FORMATS = {
    fmt.geometry.name: fmt for fmt in (translation.STAGE2_FORMAT, translation.STAGE1_FORMAT)
}


def _field(info: elfsym.TypeInfo, name: str) -> elfsym.Field:
    for member in info.fields:
        if member.name == name:
            return member
    raise KeyError(f"{info.name or info.kind} has no field {name!r}")


def _word(reader, pa: int) -> int:
    return int.from_bytes(reader.read_bytes(pa, translation.DESCRIPTOR_BYTES), "little")


_ROLE_WORD = {"cpu": "CPU", "dma": "DMA", "self": "self", "el1.low": "EL1 low", "el1.high": "EL1 high"}

# SCTLR_EL1.M — the guest's own MMU. Without it a VA is an IPA and there
# is no Stage 1 regime to offer.
_SCTLR_M = 1
# TTBR holds an ASID above the table address, and the table is aligned.
_TTBR_BADDR = ((1 << 48) - 1) & ~0xFFF


def _limit(pool_bytes: int, fmt: translation.Format) -> int:
    """How many tables a walk of this pool may read."""
    return pool_bytes // fmt.geometry.table_bytes


def _word_of(regime: dict, name: str) -> int:
    """One register out of a published EL1 bank, which travels as hex."""
    try:
        return int(str(regime.get(name, "0")), 16)
    except ValueError:
        return 0

# Whether this regime's tables hold still for the run. `CAPTURED` ones
# are copied once and walked from the copy, live or in replay; a `LIVE`
# one is walked at the moment it is asked about, and a replay answers
# only what that moment recorded.
CAPTURED = "captured"
LIVE = "live"


def _regime(
    role: str,
    vm: int | None,
    kind: str,
    root: int | None,
    tables: int,
    *,
    ground: str = CAPTURED,
    vcpu: int | None = None,
) -> dict:
    """One translation, named for who drives it.

    `tables` is the pool behind this root, which is the walk's limit —
    zero where there is no pool, which is also where there is no tree.

    A regime is keyed by whichever unit owns the register that roots it:
    VTTBR and a stream table entry are the VM's, TTBR_EL1 is a vCPU's, so
    two vCPUs of one VM are two regimes and the same address in them is
    two questions. `space` says so, and `beside` compares only within one.

    `root` is absent for a live ground: the register moves, so what would
    be on the topology is a value that was true once. It is read at the
    moment a walk is asked for instead.
    """
    who = "el2" if vm is None else f"vm{vm}"
    owner = "EL2" if vm is None else f"VM {vm}"
    if vcpu is not None:
        who, owner = f"{who}.v{vcpu}", f"{owner} · vCPU {vcpu}"
    record = {
        "id": f"{who}.{role}",
        "label": f"{owner} · {_ROLE_WORD[role]}",
        "role": role,
        "vm": vm,
        "vcpu": vcpu,
        "kind": kind,
        "tables": tables,
        "ground": ground,
        "space": f"{who}.va" if vcpu is not None or vm is None else f"{who}.ipa",
    }
    if root is not None:
        record["root"] = f"{root:#x}"
    return record


def _stage2_regimes(reader, resolved: dict[str, elfsym.ResolvedSymbol]) -> list[dict]:
    vttbr = resolved[observe.VTTBR]
    pool = resolved[observe.STAGE2_SETS].type.element.size
    fmt = translation.STAGE2_FORMAT
    regimes = []
    for vm in range(vttbr.type.count):
        # Zero is a guest whose tables were never built, not a root at
        # address zero: the array is cleared and filled per live guest.
        value = _word(reader, vttbr.address + vm * vttbr.type.element.size)
        if value:
            regimes.append(_regime("cpu", vm, fmt.geometry.name, value & fmt.output_mask, _limit(pool, fmt)))
    return regimes


def _dma_regimes(reader, resolved: dict[str, elfsym.ResolvedSymbol]) -> list[dict]:
    contexts = resolved[observe.DMA_CONTEXTS]
    pool = resolved[observe.DMA_TABLES].type.element.size
    entry = contexts.type.element
    owner = _field(entry, "owner_vm")
    root = _field(entry, "root_pa")
    fmt = translation.STAGE2_FORMAT
    count = min(_word(reader, resolved[observe.DMA_CONTEXT_COUNT].address), contexts.type.count)
    regimes = []
    for index in range(count):
        at = contexts.address + index * entry.size
        vm = int.from_bytes(reader.read_bytes(at + owner.offset, owner.type.size), "little")
        regimes.append(
            _regime("dma", vm, fmt.geometry.name, _word(reader, at + root.offset), _limit(pool, fmt))
        )
    return regimes


def _el2_regime(resolved: dict[str, elfsym.ResolvedSymbol]) -> dict:
    """EL2's own translation: its root table plus the builder's pool."""
    root = resolved[observe.EL2_ROOT]
    pool = resolved[observe.EL2_POOL]
    fmt = translation.STAGE1_FORMAT
    return _regime("self", None, fmt.geometry.name, root.address, _limit(root.size + pool.size, fmt))


def _guest_stage1_regimes(banks: list[dict] | None) -> list[dict]:
    """Every guest half that is translating right now.

    Listed only where the guest has left it on — its MMU enabled, that
    half not disabled, its granule one the architecture defines — because
    a half the guest told the hardware to fault on is not a regime to
    offer, and a bank never taken is not a half anyone knows about.

    What is listed is that it exists. Where it is rooted moves with the
    process the vCPU is running, so it is read when a walk asks.
    """
    listed = []
    for slot, bank in enumerate(banks or []):
        held = bank.get("el1", {}) if isinstance(bank, dict) else {}
        for high in (False, True):
            geometry = _guest_geometry_of(held, high)
            if geometry is None:
                continue
            listed.append(
                _regime(
                    f"el1.{'high' if high else 'low'}",
                    slot // _VCPUS_PER_VM,
                    geometry.name,
                    None,
                    0,
                    ground=LIVE,
                    vcpu=slot % _VCPUS_PER_VM,
                )
            )
    return listed


def _guest_geometry_of(held: dict, high: bool) -> translation.Geometry | None:
    """This half's geometry, or None when the guest is not using it."""
    if not _word_of(held, "sctlr") & _SCTLR_M:
        return None
    tcr = _word_of(held, "tcr")
    if not translation.guest_half_enabled(tcr, high):
        return None
    try:
        return translation.guest_geometry(tcr, high)
    except ValueError:
        # A granule the architecture reserves. The guest wrote it, so
        # this is an input rather than a fault: there is no regime to
        # describe, which is what an absent entry says.
        return None


def roster(reader, resolved: dict[str, elfsym.ResolvedSymbol], banks: list[dict] | None = None) -> list[dict]:
    """Which regimes this run has, right now.

    Cheap enough to ask every poll, which is the point: EL2's are there
    from init, and a guest's appear when it enables its own MMU.
    """
    if not resolved:
        return []
    stage2 = _stage2_regimes(reader, resolved)
    if not stage2:
        return []  # EL2 has not built its tables; nothing else is real yet either
    return [*stage2, *_dma_regimes(reader, resolved), _el2_regime(resolved), *_guest_stage1_regimes(banks)]


def copy_tables(reader, resolved: dict[str, elfsym.ResolvedSymbol]) -> dict | None:
    """Every table of every captured regime, once, as the wire carries it.

    None for a reader with no image behind it, which resolves nothing and
    has no map.

    Only words that are set travel; an empty slot is the invalid
    descriptor and the extents say where the zeros were, so what arrives
    is the same bytes rather than fewer of them.
    """
    if not resolved:
        return None
    words = {}
    extents = []
    for symbol in observe.TABLES:
        entry = resolved[symbol]
        extents.append([f"{entry.address:#x}", entry.size])
        raw = reader.read_bytes(entry.address, entry.size)
        for offset in range(0, entry.size, translation.DESCRIPTOR_BYTES):
            value = int.from_bytes(raw[offset : offset + translation.DESCRIPTOR_BYTES], "little")
            if value:
                words[f"{entry.address + offset:#x}"] = f"{value:#x}"
    return {"extents": extents, "words": words}


@dataclass(frozen=True)
class Tables:
    """A `MemoryReader` over a captured copy.

    The walker's only source live and in replay alike, so a replay
    cannot answer differently from the run it recorded.
    """

    words: dict[int, int]
    extents: tuple[tuple[int, int], ...]

    @classmethod
    def of(cls, captured: dict) -> Tables:
        return cls(
            {int(at, 16): int(word, 16) for at, word in captured.get("words", {}).items()},
            tuple((int(at, 16), size) for at, size in captured.get("extents", [])),
        )

    def read_bytes(self, pa: int, size: int) -> bytes:
        """Bytes from the copy, or nothing at all.

        A read outside what was captured fails rather than returning
        zeros — zeros are invalid descriptors, and a walk given them
        would call an address unmapped when the copy is merely short.
        Reads are descriptor-aligned because that is all a table holds;
        an unaligned one would land between words and come back empty.
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


def _address(value) -> int:
    """A probe target, from whatever the client typed.

    Always hex, prefix or not: reading a bare string as decimal would
    answer a different question and look ordinary doing it.
    """
    try:
        return int(str(value).strip().replace("_", ""), 16)
    except ValueError:
        raise ValueError(f"{value!r} is not an address") from None


class _Walks:
    """One walk per regime per request, kept for whoever asks next.

    The chosen regime's tree and both sides of the isolation difference
    overlap; walking twice would be two places building one answer.

    Which bytes a regime is walked over is the regime's own answer: a
    captured ground reads the copy, live or in replay, and a live one
    reads this moment's memory through the guest's Stage 2 — so a replay,
    which has no memory, cannot walk it and says so instead.
    """

    def __init__(self, topology: dict, live=None, banks: list[dict] | None = None):
        self.copy = Tables.of(topology)
        self._live = live
        self._banks = banks or []
        self._regimes = topology["regimes"]
        self._trees: dict[str, translation.Tree] = {}

    def _bank(self, regime: dict) -> dict:
        """The EL1 bank this regime is rooted in, as published now."""
        slot = regime["vm"] * _VCPUS_PER_VM + regime["vcpu"]
        held = self._banks[slot] if slot < len(self._banks) else {}
        bank = held.get("el1", {}) if isinstance(held, dict) else {}
        if _guest_geometry_of(bank, regime["role"].endswith("high")) is None:
            raise ValueError(f"{regime['id']} is not translating now")
        return bank

    def format(self, regime: dict) -> translation.Format:
        if regime["ground"] == CAPTURED:
            return FORMATS[regime["kind"]]
        high = regime["role"].endswith("high")
        return translation.replace(
            translation.STAGE1_FORMAT, geometry=_guest_geometry_of(self._bank(regime), high)
        )

    def root(self, regime: dict) -> int:
        if regime["ground"] == CAPTURED:
            return int(regime["root"], 16)
        high = regime["role"].endswith("high")
        return _word_of(self._bank(regime), "ttbr1" if high else "ttbr0") & _TTBR_BADDR

    def beneath(self, regime: dict) -> dict:
        """The translation this regime's output is an input to."""
        found = next(
            (other for other in self._regimes if other["vm"] == regime["vm"] and other["role"] == "cpu"),
            None,
        )
        if found is None:
            raise ValueError(f"{regime['id']} has no Stage 2 under it")
        return found

    def reader(self, regime: dict):
        """The bytes this regime's tables are in."""
        if regime["ground"] == CAPTURED:
            return self.copy
        if self._live is None:
            raise ValueError(f"{regime['id']} is walked as it is asked, and a replay has no memory to walk")
        return translation.GuestReader(self._live, self.root(self.beneath(regime)))

    def tree(self, regime: dict) -> translation.Tree:
        if regime["id"] not in self._trees:
            self._trees[regime["id"]] = translation.tree(
                self.reader(regime), self.format(regime), self.root(regime), regime["tables"]
            )
        return self._trees[regime["id"]]

    def probe(self, regime: dict, address: int) -> translation.Probe:
        return translation.probe(self.reader(regime), self.format(regime), self.root(regime), address)


def _windows(nodes: tuple[translation.Node, ...], geometry: translation.Geometry) -> list[tuple[int, int]]:
    """Every input range a walk ends in, merged and in order.

    Adjacent runs merge: the question is which addresses are reachable,
    and a boundary between two reachable regions is an artefact of how
    the builder laid them down.
    """
    spans: list[tuple[int, int]] = []
    for node in nodes:
        if node.children:
            spans += _windows(node.children, geometry)
        elif node.descriptor.maps:
            spans.append((node.base, node.base + node.count * geometry.span(node.depth)))
    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _minus(spans, other) -> list[list[str]]:
    """What the first side reaches and the second does not."""
    out = []
    for start, end in spans:
        at = start
        for cut_start, cut_end in other:
            if cut_end <= at or cut_start >= end:
                continue
            if cut_start > at:
                out.append([f"{at:#x}", f"{cut_start - at:#x}"])
            at = max(at, cut_end)
        if at < end:
            out.append([f"{at:#x}", f"{end - at:#x}"])
    return out


def _siblings(captured: dict, regime: dict) -> list[dict]:
    """This VM's other translations. EL2 has none: its `vm` is null."""
    if regime["vm"] is None:
        return []
    return [
        other
        for other in captured["regimes"]
        if other["vm"] == regime["vm"] and other["id"] != regime["id"]
    ]


def _isolation(walks: _Walks, captured: dict, regime: dict) -> dict | None:
    """One VM's two Stage 2 translations, by their difference.

    They are separate table sets rather than one with an overlay, so the
    reading is where they disagree: a window only the CPU reaches is
    memory no device can touch, one only DMA reaches is a device able to
    write where the guest cannot look.
    """
    sides = {
        entry["role"]: entry
        for entry in (regime, *_siblings(captured, regime))
        if entry["role"] in ("cpu", "dma")
    }
    if len(sides) < 2:
        return None
    reach = {
        role: _windows(walks.tree(side).nodes, walks.format(side).geometry)
        for role, side in sides.items()
    }
    return {
        "cpu": sides["cpu"]["id"],
        "dma": sides["dma"]["id"],
        "cpu_only": _minus(reach["cpu"], reach["dma"]),
        "dma_only": _minus(reach["dma"], reach["cpu"]),
    }


def answer(topology: dict, request: dict, live=None, banks: list[dict] | None = None) -> dict:
    """Walk one regime for a client.

    A captured regime is read from the copy live and in replay alike, so
    the walk is not reimplemented on either side of a recording. A live
    one is rooted and read now, through the guest's Stage 2, and carries
    no tree: its map is thousands of tables and the question a reader has
    is about one address. The root travels with the answer either way,
    because for a live regime it is part of what was asked.
    """
    wanted = str(request.get("regime", ""))
    regime = next((entry for entry in topology["regimes"] if entry["id"] == wanted), None)
    if regime is None:
        raise KeyError(f"no regime {wanted!r}")
    walks = _Walks(topology, live, banks)
    fmt = walks.format(regime)
    data = {"regime": regime["id"], "ground": regime["ground"], "root": f"{walks.root(regime):#x}"}
    if regime["ground"] == CAPTURED:
        data["tree"] = _tree_wire(walks.tree(regime), fmt)
        isolation = _isolation(walks, topology, regime)
        if isolation is not None:
            data["isolation"] = isolation
    if request.get("address") in (None, ""):
        return data
    at = _address(request["address"])
    found = walks.probe(regime, at)
    data["probe"] = _probe_wire(found, fmt)
    if regime["ground"] == LIVE:
        # A guest rewrites these tables as it runs, and a walk reads one
        # level at a time: the answer can be two moments spliced. A single
        # descriptor is one word and cannot tear, so what is checked is the
        # chain — walked again, and reported as differing rather than
        # retried, because how many tries would settle it is not a number
        # anyone knows.
        data["moving"] = walks.probe(regime, at) != found
        if found.output is not None:
            # The output is an IPA, which is an input to the translation
            # beneath. Carried here so one answer closes VA to PA rather
            # than asking the reader to hold half of it.
            beneath = walks.beneath(regime)
            data["through"] = {
                "regime": beneath["id"],
                "label": beneath["label"],
                "probe": _probe_wire(walks.probe(beneath, found.output), walks.format(beneath)),
            }
    # The same address in this VM's other translations — only those where
    # it is the same question. Across address spaces the number would
    # decode into a plausible answer to something nobody asked.
    data["beside"] = [
        {
            "regime": other["id"],
            "label": other["label"],
            "probe": _probe_wire(walks.probe(other, at), walks.format(other)),
        }
        for other in _siblings(topology, regime)
        if other["space"] == regime["space"]
    ]
    return data


def _wx_slots(nodes: tuple[translation.Node, ...]) -> int:
    """Slots this map makes both writable and executable.

    A count, not a verdict: whether it is a defect is the regime's
    answer, carried beside it as `wxn`.
    """
    total = 0
    for node in nodes:
        if node.descriptor.writable and node.descriptor.executable:
            total += node.count
        total += _wx_slots(node.children)
    return total


def _rights(descriptor: translation.Descriptor) -> dict:
    """What may be done where a walk ends.

    One spelling for the tree row and the probe answer, so the two can
    be compared without either naming a field differently.
    """
    return {
        "w": descriptor.writable,
        "x": descriptor.executable,
        "memory": descriptor.memory,
    }


def _tree_wire(found: translation.Tree, fmt: translation.Format) -> dict:
    return {
        "root": f"{found.root:#x}",
        "read": found.read,
        "truncated": found.truncated,
        "unreadable": [f"{pa:#x}" for pa in found.unreadable],
        "wx": _wx_slots(found.nodes),
        "wxn": fmt.wxn,
        "nodes": [_node_wire(node, fmt) for node in found.nodes],
    }


def _node_wire(node: translation.Node, fmt: translation.Format) -> dict:
    """One row of the map: a run of slots, with the span it covers.

    The span rather than the level's shift, because a client deriving it
    would hold a second copy of the geometry.
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
        wire |= _rights(descriptor) | {"af": descriptor.accessed}
    if node.children:
        wire["children"] = [_node_wire(child, fmt) for child in node.children]
    return wire


def _probe_wire(found: translation.Probe, fmt: translation.Format) -> dict:
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
    if found.output is not None:
        # Half of what an address means is the permission the walk ended
        # on, so the answer carries it rather than sending the reader
        # back into the tree.
        wire |= _rights(found.steps[-1].descriptor)
    return wire
