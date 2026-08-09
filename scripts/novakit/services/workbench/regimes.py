"""The translation regimes a run has, and the tables behind them.

These tables are written during EL2 init and never again — the same fact
that lets `nova_stage2_switch` retarget VTTBR without invalidating a
TLB — so one copy per run is all of them rather than a sample. The copy
rides the topology and the walker reads it instead of RAM, which is how
a late joiner and a replay with no RAM get the same bytes.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...image import elfsym, observe
from . import translation

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


_ROLE_WORD = {"cpu": "CPU", "dma": "DMA", "self": "self"}


def _regime(role: str, vm: int | None, fmt: translation.Format, root: int, pool_bytes: int) -> dict:
    """One translation, named for who drives it.

    `tables` is the pool behind this root, which is the walk's limit.
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
    vttbr = resolved[observe.VTTBR]
    pool = resolved[observe.STAGE2_SETS].type.element.size
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
    contexts = resolved[observe.DMA_CONTEXTS]
    pool = resolved[observe.DMA_TABLES].type.element.size
    entry = contexts.type.element
    owner = _field(entry, "owner_vm")
    root = _field(entry, "root_pa")
    count = min(_word(reader, resolved[observe.DMA_CONTEXT_COUNT].address), contexts.type.count)
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
    root = resolved[observe.EL2_ROOT]
    pool = resolved[observe.EL2_POOL]
    return _regime("self", None, translation.STAGE1_FORMAT, root.address, root.size + pool.size)


def capture(reader, resolved: dict[str, elfsym.ResolvedSymbol]) -> dict | None:
    """Read every root and every table once, as the wire carries them.

    None until EL2 has built the tables: the RAM backend exists from the
    moment QEMU starts, and an early read would publish a page of zeros
    as the machine's whole address map. None too for a reader with no
    image behind it, which resolves nothing and has no map.

    Only words that are set travel; an empty slot is the invalid
    descriptor and the extents say where the zeros were, so what arrives
    is the same bytes rather than fewer of them.
    """
    if not resolved:
        return None
    regimes = _stage2_regimes(reader, resolved)
    if not regimes:
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
    return {
        "regimes": [*regimes, *_dma_regimes(reader, resolved), _el2_regime(resolved)],
        "extents": extents,
        "words": words,
    }


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
            {int(at, 16): int(word, 16) for at, word in captured["words"].items()},
            tuple((int(at, 16), size) for at, size in captured["extents"]),
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


def answer(captured: dict, request: dict) -> dict:
    """Walk one regime for a client.

    Reads the captured tables live and in replay alike, so the walk is
    not reimplemented on either side of a recording.
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
    # The same address in this VM's other translation. Asking for the
    # second in its own request would let the two answers be about
    # different addresses.
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
