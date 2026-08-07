"""The S layer: polled snapshots of firmware state, behind a seam.

`SnapshotProvider` is the swappable boundary: today it is DWARF-described
reads from the mmapped guest RAM file; a firmware-published telemetry
block can later implement the same shape without touching the poller or
anything downstream of the store.
"""

from __future__ import annotations

import mmap
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ...image import abi
from . import elfsym
from .observations import OBSERVATIONS, Obs

_U32 = elfsym.TypeInfo("uint", 4)
_U64 = elfsym.TypeInfo("uint", 8)

# Guest memory carries no DWARF, so the IVC page is the one layout the
# decoder is told rather than shown. It is told by the ABI header the
# hypervisor's ring and the guest helper both compile against, so the
# three cannot disagree.
_IVC = abi.read_defines(
    abi.IVC_RING,
    [
        "NOVA_IVC_RING0_OFF",
        "NOVA_IVC_RING1_OFF",
        "NOVA_IVC_RING_WIDX_OFF",
        "NOVA_IVC_RING_RIDX_OFF",
        "NOVA_IVC_RING_SLOTS_OFF",
        "NOVA_IVC_RING_SLOTS",
    ],
)
_SLOT_COUNT = _IVC["NOVA_IVC_RING_SLOTS"]
# The rings are laid end to end, so their spacing is the ring's stride.
_RING_STRIDE = _IVC["NOVA_IVC_RING1_OFF"] - _IVC["NOVA_IVC_RING0_OFF"]
_SLOTS = elfsym.TypeInfo("array", _SLOT_COUNT * _U64.size, element=_U64, count=_SLOT_COUNT)
_IVC_RING = elfsym.TypeInfo(
    "struct",
    _RING_STRIDE,
    name="ivc_ring",
    fields=(
        elfsym.Field("widx", _IVC["NOVA_IVC_RING_WIDX_OFF"], _U32),
        elfsym.Field("ridx", _IVC["NOVA_IVC_RING_RIDX_OFF"], _U32),
        elfsym.Field("slots", _IVC["NOVA_IVC_RING_SLOTS_OFF"], _SLOTS),
    ),
)
PAGE_LAYOUTS: dict[str, elfsym.TypeInfo] = {
    "ivc_ring_page": elfsym.TypeInfo(
        "struct",
        abi.read_define(abi.GUEST_LAYOUT, "NOVA_IVC_SHM_SIZE"),
        name="ivc_page",
        fields=(
            elfsym.Field("ring0", _IVC["NOVA_IVC_RING0_OFF"], _IVC_RING),
            elfsym.Field("ring1", _IVC["NOVA_IVC_RING1_OFF"], _IVC_RING),
        ),
    ),
}


def _hexify(value):
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:#x}"
    if isinstance(value, list):
        return [_hexify(item) for item in value]
    if isinstance(value, dict):
        return {key: _hexify(item) for key, item in value.items()}
    return value


class SnapshotProvider(Protocol):
    def read(self, obs: Obs) -> object: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class ImageView:
    """Everything the S layer needs from an image, holding nothing open.

    Plain data by construction — resolved addresses, decoded type
    layouts, and the symbol table — so producing it is separable from
    using it. That matters because producing it is three seconds of
    pure Python that lands during guest boot, which is the busiest the
    trace rings ever get: work this shape can be sent somewhere it
    cannot compete with the drain.
    """

    resolved: dict[str, elfsym.ResolvedSymbol]
    symbols: elfsym.SymbolTable


def resolve_image(elf_path: Path) -> ImageView:
    """Resolve every observation against an image.

    Pure: opens the ELF, reads it, closes it, and returns data. No mmap
    of guest RAM and no live handle in the result, so the caller is free
    to run this in another process.
    """
    index = elfsym.ElfIndex(elf_path)
    try:
        return ImageView(
            {obs.topic: index.resolve(obs.symbol) for obs in OBSERVATIONS if obs.pa is None},
            index.symbols,
        )
    finally:
        index.close()


class ElfRamProvider:
    """Decode firmware globals straight out of the shared RAM file.

    `ram_base` is where the machine's RAM aperture starts: QEMU backs
    exactly that span with this file, so a physical address is read at
    `pa - ram_base`. It is a board fact, and the caller supplies it.

    A caller that already has the image resolved passes it in; the
    provider itself never holds the ELF open past construction.
    """

    def __init__(self, elf_path: Path, ram_path: Path, ram_base: int, view: ImageView | None = None):
        self._base = ram_base
        image = resolve_image(elf_path) if view is None else view
        self._resolved = image.resolved
        self._symbols = image.symbols
        with ram_path.open("rb") as backing:
            self._ram = mmap.mmap(backing.fileno(), 0, prot=mmap.PROT_READ)
        highest = max(entry.address + entry.size for entry in self._resolved.values())
        # PA-declared pages sit far above the image (IVC at +512 MiB);
        # a short backend must fail here, not decode as silent zeros.
        for obs in OBSERVATIONS:
            if obs.pa is not None:
                highest = max(highest, obs.pa + PAGE_LAYOUTS[obs.layout].size)
        if len(self._ram) < highest - self._base:
            self.close()
            raise ValueError("RAM backend is smaller than the observed image")

    def read(self, obs: Obs) -> object:
        if obs.pa is not None:
            info = PAGE_LAYOUTS[obs.layout]
            offset = obs.pa - self._base
            value = elfsym.decode(info, self._ram[offset : offset + info.size])
        else:
            resolved = self._resolved[obs.topic]
            info = resolved.type
            offset = resolved.address - self._base
            value = elfsym.decode(
                info, self._ram[offset : offset + resolved.size], fields=obs.fields
            )
        # Encodings become meanings before the wire, so no reader has to
        # know the width of a "none" or the layout of a packed word.
        if obs.shape is not None:
            value = obs.shape(value, info)
        return _hexify(value) if obs.hex else value

    @property
    def symbols(self) -> elfsym.SymbolTable:
        """What this image carries, for questions about capability.

        The symtab is already parsed behind this provider, so asking it
        costs a dictionary lookup — where opening the ELF again to ask
        the same thing costs a third of a second.
        """
        return self._symbols

    def close(self) -> None:
        self._ram.close()


def changed_paths(before, after, prefix: str = "") -> list[str]:
    """Leaf paths that differ between two readings of one topic.

    What a stop is *for* is seeing what moved, and a stop publishes the
    whole machine — twenty-eight topics of it. Between two consecutive
    binds three or four values actually changed, and finding them by eye
    across every panel is the work this removes.

    Leaves, not containers: "the scheduler changed" is true of almost
    every stop and says nothing, where `sched.cpu[1].current` is the
    answer. A path that appears on one side only is a change too — a
    reading that grew or lost a field is exactly the kind of thing worth
    being told about.
    """
    if isinstance(before, dict) and isinstance(after, dict):
        out = []
        for key in dict.fromkeys([*before, *after]):
            out += changed_paths(before.get(key), after.get(key), f"{prefix}.{key}" if prefix else key)
        return out
    if isinstance(before, list) and isinstance(after, list):
        out = []
        for index in range(max(len(before), len(after))):
            here = before[index] if index < len(before) else None
            there = after[index] if index < len(after) else None
            out += changed_paths(here, there, f"{prefix}[{index}]")
        return out
    return [] if before == after else [prefix or "value"]


def image_symbols(provider) -> elfsym.SymbolTable | None:
    """The symbol table behind a provider, if it has one.

    A scripted provider has no image, and a capability question it
    cannot answer is unknown rather than no.
    """
    return getattr(provider, "symbols", None)


class SnapshotPoller:
    """Rate-limit each observation and report only changed values.

    A torn enum read aborts that observation's tick silently — the value
    was mid-write and the next tick sees a consistent one.
    """

    def __init__(
        self,
        provider: SnapshotProvider,
        observations: Sequence[Obs] = OBSERVATIONS,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self._provider = provider
        self._observations = tuple(observations)
        self._monotonic = monotonic
        self._due = dict.fromkeys((obs.topic for obs in self._observations), 0.0)
        self._last: dict[str, object] = {}

    def tick(self) -> list[tuple[Obs, object]]:
        now = self._monotonic()
        changes = []
        for obs in self._observations:
            if now < self._due[obs.topic]:
                continue
            self._due[obs.topic] = now + 1.0 / obs.rate_hz
            try:
                value = self._provider.read(obs)
            except elfsym.TornRead:
                continue
            if self._last.get(obs.topic) != value:
                self._last[obs.topic] = value
                changes.append((obs, value))
        return changes

    def sweep(self) -> list[tuple[Obs, object]]:
        """Every observation at once, rate and change gate ignored.

        Only meaningful while the machine is stopped. Then nothing is
        moving, so these reads are all of one instant — no torn value, no
        writer racing the reader — and the point is the complete picture
        rather than the delta the gate exists to find. A reader who has
        just arrived at an event wants the whole machine, including every
        field that happens not to have changed since the last poll.

        The cache is updated, so resuming does not replay as changes the
        values this already sent.
        """
        values = []
        for obs in self._observations:
            try:
                value = self._provider.read(obs)
            except elfsym.TornRead:
                continue
            self._last[obs.topic] = value
            values.append((obs, value))
        return values
