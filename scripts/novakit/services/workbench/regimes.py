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

from . import elfsym, observations, translation

# Every regime this run has, by the format its descriptors are in. The
# key is what the wire calls the regime's kind, so the two cannot drift.
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


_ROLE_WORD = {"cpu": "CPU", "dma": "DMA", "self": "self"}


def _regime(role: str, vm: int | None, fmt: translation.Format, root: int, pool_bytes: int) -> dict:
    """One translation, named for who drives it.

    `tables` is how many the pool behind this root holds, which is the
    walk's limit: wanting more means following a word that is not a
    table pointer.
    """
    who = "el2" if vm is None else f"vm{vm}"
    owner = "EL2" if vm is None else f"VM {vm}"
    return {
        "id": f"{who}.{role}",
        "label": f"{owner} · {_ROLE_WORD[role]}",
        "role": role,
        "vm": vm,
        "kind": fmt.geometry.name,
        "root": f"{root:#x}",
        "tables": pool_bytes // fmt.geometry.table_bytes,
    }


def _stage2_regimes(reader, resolved: dict[str, elfsym.ResolvedSymbol]) -> list[dict]:
    vttbr = resolved[observations.VTTBR]
    pool = resolved[observations.STAGE2_SETS].type.element.size
    fmt = translation.STAGE2_FORMAT
    regimes = []
    for vm in range(vttbr.type.count):
        # Zero is a guest whose tables were never built, not a root at
        # address zero: the array is cleared and filled per live guest.
        value = _word(reader, vttbr.address + vm * vttbr.type.element.size)
        if value:
            regimes.append(_regime("cpu", vm, fmt, value & fmt.output_mask, pool))
    return regimes


def _dma_regimes(reader, resolved: dict[str, elfsym.ResolvedSymbol]) -> list[dict]:
    contexts = resolved[observations.DMA_CONTEXTS]
    pool = resolved[observations.DMA_TABLES].type.element.size
    entry = contexts.type.element
    owner = _field(entry, "owner_vm")
    root = _field(entry, "root_pa")
    count = min(_word(reader, resolved[observations.DMA_CONTEXT_COUNT].address), contexts.type.count)
    regimes = []
    for index in range(count):
        at = contexts.address + index * entry.size
        vm = int.from_bytes(reader.read_bytes(at + owner.offset, owner.type.size), "little")
        regimes.append(
            _regime("dma", vm, translation.STAGE2_FORMAT, _word(reader, at + root.offset), pool)
        )
    return regimes


def _el2_regime(resolved: dict[str, elfsym.ResolvedSymbol]) -> dict:
    """EL2's own translation: its root table plus the builder's pool."""
    root = resolved[observations.EL2_ROOT]
    pool = resolved[observations.EL2_POOL]
    return _regime("self", None, translation.STAGE1_FORMAT, root.address, root.size + pool.size)


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
    for symbol in observations.TABLES:
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


def _address(value) -> int:
    """A probe target, from whatever the client typed.

    Always hex, prefix or not: reading a bare string as decimal would
    answer a different question and look perfectly ordinary doing it.
    """
    try:
        return int(str(value).strip().replace("_", ""), 16)
    except ValueError:
        raise ValueError(f"{value!r} is not an address") from None


class _Walks:
    """One walk per regime per request, kept for whoever asks next.

    A request wants the chosen regime's tree and, for a VM, both sides of
    the isolation difference — which overlap. Walking twice would be the
    same tables read twice and, worse, two places building one answer.
    """

    def __init__(self, captured: dict):
        self.reader = Tables.of(captured)
        self._trees: dict[str, translation.Tree] = {}

    def format(self, regime: dict) -> translation.Format:
        return FORMATS[regime["kind"]]

    def root(self, regime: dict) -> int:
        return int(regime["root"], 16)

    def tree(self, regime: dict) -> translation.Tree:
        if regime["id"] not in self._trees:
            self._trees[regime["id"]] = translation.tree(
                self.reader, self.format(regime), self.root(regime), regime["tables"]
            )
        return self._trees[regime["id"]]

    def probe(self, regime: dict, address: int) -> translation.Probe:
        return translation.probe(self.reader, self.format(regime), self.root(regime), address)


def _windows(nodes: tuple[translation.Node, ...], geometry: translation.Geometry) -> list[tuple[int, int]]:
    """Every input range a walk ends in, merged and in order.

    Where a run of slots is one range, adjacent runs that happen to abut
    become one too: what matters here is which addresses are reachable,
    and a boundary between two identically-reachable regions is an
    artefact of how the builder laid them down.
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

    They are separate table sets rather than one with an overlay, so what
    is worth looking at is not where they agree but where they do not. A
    window only the CPU reaches is memory no device can touch; one only
    DMA reaches is a device able to write where the guest cannot look.
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
    walks = _Walks(captured)
    fmt = walks.format(regime)
    data = {"regime": regime["id"], "tree": _tree_wire(walks.tree(regime), fmt)}
    isolation = _isolation(walks, captured, regime)
    if isolation is not None:
        data["isolation"] = isolation
    if request.get("address") in (None, ""):
        return data
    at = _address(request["address"])
    data["probe"] = _probe_wire(walks.probe(regime, at), fmt)
    # The same address in this VM's other translation. One number, two
    # answers: that is the comparison, and asking for the second in a
    # separate request would let the two be about different addresses.
    data["beside"] = [
        {
            "regime": other["id"],
            "label": other["label"],
            "probe": _probe_wire(walks.probe(other, at), walks.format(other)),
        }
        for other in _siblings(captured, regime)
    ]
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


def _rights(descriptor: translation.Descriptor) -> dict:
    """What may be done where a walk ends.

    One spelling for the tree row and the probe answer, so a reader can
    compare the two without either side naming a field differently.
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
    """One row of the map.

    Carries the span it covers rather than the level's shift: a client
    deriving that would hold a second copy of the geometry.
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
        # back into the tree to look it up.
        wire |= _rights(found.steps[-1].descriptor)
    return wire
