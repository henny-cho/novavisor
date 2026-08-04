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

from . import elfsym
from .observations import OBSERVATIONS, Obs

# The board's RAM base: the identity-mapped image loads here, so a
# physical address maps to (pa - RAM_BASE) inside the backend file.
RAM_BASE = 0x4000_0000

_U32 = elfsym.TypeInfo("uint", 4)
_U64 = elfsym.TypeInfo("uint", 8)
_SLOTS = elfsym.TypeInfo("array", 128, element=_U64, count=16)
# Mirrors nova/abi/ivc_ring.h: two SPSC rings, cache-line separated
# indices, 16 slots of u64 each. Guest memory has no DWARF, so the
# layout is declared here and held to the ABI by its own constants.
_IVC_RING = elfsym.TypeInfo(
    "struct",
    0x800,
    name="ivc_ring",
    fields=(
        elfsym.Field("widx", 0x00, _U32),
        elfsym.Field("ridx", 0x40, _U32),
        elfsym.Field("slots", 0x80, _SLOTS),
    ),
)
PAGE_LAYOUTS: dict[str, elfsym.TypeInfo] = {
    "ivc_ring_page": elfsym.TypeInfo(
        "struct",
        0x1000,
        name="ivc_page",
        fields=(
            elfsym.Field("ring0", 0x000, _IVC_RING),
            elfsym.Field("ring1", 0x800, _IVC_RING),
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
    """Decode firmware globals straight out of the shared RAM file."""

    def __init__(self, elf_path: Path, ram_path: Path):
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
        if len(self._ram) < highest - RAM_BASE:
            self.close()
            raise ValueError("RAM backend is smaller than the observed image")

    def read(self, obs: Obs) -> object:
        if obs.pa is not None:
            info = PAGE_LAYOUTS[obs.layout]
            offset = obs.pa - RAM_BASE
            value = elfsym.decode(info, self._ram[offset : offset + info.size])
        else:
            resolved = self._resolved[obs.topic]
            offset = resolved.address - RAM_BASE
            value = elfsym.decode(
                resolved.type,
                self._ram[offset : offset + resolved.size],
                fields=obs.fields,
            )
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
