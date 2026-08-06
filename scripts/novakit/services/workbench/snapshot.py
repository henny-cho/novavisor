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


class ElfRamProvider:
    """Decode firmware globals straight out of the shared RAM file.

    `ram_base` is where the machine's RAM aperture starts: QEMU backs
    exactly that span with this file, so a physical address is read at
    `pa - ram_base`. It is a board fact, and the caller supplies it.
    """

    def __init__(self, elf_path: Path, ram_path: Path, ram_base: int):
        self._base = ram_base
        self._index = elfsym.ElfIndex(elf_path)
        try:
            self._resolved = {
                obs.topic: self._index.resolve(obs.symbol)
                for obs in OBSERVATIONS
                if obs.pa is None
            }
            with ram_path.open("rb") as backing:
                self._ram = mmap.mmap(backing.fileno(), 0, prot=mmap.PROT_READ)
        except BaseException:
            self._index.close()
            raise
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

    def close(self) -> None:
        self._ram.close()
        self._index.close()


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
