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

from ...image import abi, elfsym, observe
from .observations import OBSERVATIONS, PUBLISH_HZ, Obs

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
# The published region's geometry, from the header the publisher and
# this reader share. Nothing here is spelled a second time.
_TLM = abi.read_defines(
    abi.TELEMETRY,
    [
        "NOVA_TLM_MAGIC",
        "NOVA_TLM_VERSION",
        "NOVA_TLM_MAGIC_OFF",
        "NOVA_TLM_VERSION_OFF",
        "NOVA_TLM_SLOTS_OFF",
        "NOVA_TLM_DESCSIZE_OFF",
        "NOVA_TLM_PERIOD_OFF",
        "NOVA_TLM_BUDGET_OFF",
        "NOVA_TLM_BYTES_OFF",
        "NOVA_TLM_HEADER_SIZE",
        "NOVA_TLM_DESC_SIZE",
        "NOVA_TLM_DESC_SOURCE_OFF",
        "NOVA_TLM_DESC_SEQ_OFF",
        "NOVA_TLM_DESC_STAMP_OFF",
        "NOVA_TLM_DESC_AT_OFF",
        "NOVA_TLM_DESC_BYTES_OFF",
    ],
)
# The publisher's own storage, named here with the observed globals: a
# rename should fail the manifest check, not quietly leave the S layer
# without a region to read.
TELEMETRY_REGION = "nova::telemetry::g_region"

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


def _u32(buffer: bytes, offset: int) -> int:
    return int.from_bytes(buffer[offset : offset + 4], "little")


def _u64(buffer: bytes, offset: int) -> int:
    return int.from_bytes(buffer[offset : offset + 8], "little")


def _hexify(value):
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:#x}"
    if isinstance(value, list):
        return [_hexify(item) for item in value]
    if isinstance(value, dict):
        return {key: _hexify(item) for key, item in value.items()}
    return value


class SnapshotProvider(Protocol):
    # When the firmware says each topic last moved, on its own clock.
    # Empty for a provider with no publisher to ask — a script, a
    # replay, a reader of raw addresses — which is a different thing
    # from a topic that has not moved yet, and reads as one.
    stamps: dict[str, int]

    def read(self, obs: Obs, *, live: bool = True) -> object: ...

    def close(self) -> None: ...


class MemoryReader(Protocol):
    """Bytes at a physical address, wherever they come from.

    Apart from `SnapshotProvider` because the questions differ: a
    snapshot is one named symbol decoded through its declared layout,
    where a walk follows addresses it learns as it reads. Apart, a
    replay serves the walk from a recorded copy without pretending it
    can also serve a live symbol read.
    """

    def read_bytes(self, pa: int, size: int) -> bytes: ...


class ElfRamProvider:
    """Decode firmware globals straight out of the shared RAM file.

    `ram_base` is where the machine's RAM aperture starts: QEMU backs
    exactly that span with this file, so a physical address is read at
    `pa - ram_base`. It is a board fact, and the caller supplies it.

    A caller that already has the image resolved passes it in; the
    provider itself never holds the ELF open past construction.
    """

    def __init__(
        self, elf_path: Path, ram_path: Path, ram_base: int, view: observe.View | None = None
    ):
        # No publisher behind these bytes, so no clock to quote for them.
        self.stamps: dict[str, int] = {}
        self._base = ram_base
        image = observe.resolve(elf_path) if view is None else view
        self._resolved = image.resolved
        self._symbols = image.symbols
        self.regimes = image.walk
        with ram_path.open("rb") as backing:
            self._ram = mmap.mmap(backing.fileno(), 0, prot=mmap.PROT_READ)
        highest = max(
            entry.address + entry.size
            for entry in (*self._resolved.values(), *self.regimes.values())
        )
        # PA-declared pages sit far above the image (IVC at +512 MiB);
        # a short backend must fail here, not decode as silent zeros.
        for obs in OBSERVATIONS:
            if obs.pa is not None:
                highest = max(highest, obs.pa + PAGE_LAYOUTS[obs.layout].size)
        if len(self._ram) < highest - self._base:
            self.close()
            raise ValueError("RAM backend is smaller than the observed image")

    def read(self, obs: Obs, *, live: bool = True) -> object:
        # This provider reads the address either way: there is no
        # publisher to ask whether the value moved, and a stopped
        # machine is what it always assumes it is reading.
        del live
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

    def read_bytes(self, pa: int, size: int) -> bytes:
        """Raw memory, for readers that learn their addresses as they go.

        Slicing an mmap past its end returns what there is instead of
        failing, so a read running off the backend is caught here: a
        half-read table decodes as invalid entries, and the walk would
        call an address unmapped when it is only unreadable.
        """
        offset = pa - self._base
        if offset < 0 or size < 0 or offset + size > len(self._ram):
            raise ValueError(f"{pa:#x}+{size:#x} is outside the mapped RAM")
        return self._ram[offset : offset + size]

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


def changed_mask(before, after):
    """What moved between two readings of one topic, shaped like the
    value itself.

    What a stop is *for* is seeing what moved, and a stop publishes the
    whole manifest. Between two consecutive binds three or four values
    actually changed, and finding them by eye across every panel is the
    work this removes.

    Leaves, not containers: "the scheduler changed" is true of almost
    every stop and says nothing, where the one cell holding `current` is
    the answer. A key present on one side only is a change too — a
    reading that grew or lost a field is exactly the kind of thing worth
    being told about — and so is one whose shape changed, which lands on
    the scalar comparison and comes back true.

    A mask rather than a list of paths like `sched.cpu[1].current`,
    because a renderer walking the value would then have to build that
    string to look itself up, and building it means implementing this
    function's grammar a second time in another language. Shaped the
    same as the value, the mask is walked with the value by the same
    indexing, and there is no grammar to agree about. List indices are
    string keys so that walking an array and walking an object are the
    same code on the far side.

    Sparse: only what moved appears, so the cost is the size of the
    answer rather than the size of the reading.
    """
    if isinstance(before, dict) and isinstance(after, dict):
        out = {}
        for key in dict.fromkeys([*before, *after]):
            inner = changed_mask(before.get(key), after.get(key))
            if inner:
                out[str(key)] = inner
        return out
    if isinstance(before, list) and isinstance(after, list):
        out = {}
        for index in range(max(len(before), len(after))):
            here = before[index] if index < len(before) else None
            there = after[index] if index < len(after) else None
            inner = changed_mask(here, there)
            if inner:
                out[str(index)] = inner
        return out
    return before != after


def moved_count(mask) -> int:
    """Leaves in a mask: how many values actually moved.

    Here rather than in the caller because the mask's shape is this
    module's, and a badge counting it by hand would be the second
    implementation the mask exists to prevent.
    """
    if mask is True:
        return 1
    if not isinstance(mask, dict):
        return 0
    return sum(moved_count(inner) for inner in mask.values())


def image_symbols(provider) -> elfsym.SymbolTable | None:
    """The symbol table behind a provider, if it has one.

    A scripted provider has no image, and a capability question it
    cannot answer is unknown rather than no.
    """
    return getattr(provider, "symbols", None)


class TelemetryProvider:
    """Decode firmware globals out of the region the firmware publishes.

    The same DWARF decode as `ElfRamProvider` over different bytes. What
    changes is not the layout — the compiler is still the only source of
    that — but where the bytes come from and what is known about them.
    They are a copy a core took at one instant, under a sequence that
    says the copy was whole, stamped with the clock the trace ring uses.

    Everything that is not a published global still goes to the reader
    underneath: the page-table walk learns its addresses as it reads, and
    the IVC page is guest memory the firmware does not publish.

    Composed over a `MemoryReader` rather than replacing it, so the day
    the bytes stop arriving by memory map, only what is underneath
    changes.
    """

    def __init__(self, inner, view: observe.View):
        self._inner = inner
        self._resolved = view.resolved
        self._symbols = view.symbols
        self.regimes = view.walk
        self._seen: dict[str, int] = {}
        # What the firmware last said about each topic, for a caller
        # placing a reading on the trace timeline.
        self.stamps: dict[str, int] = {}

        base = view.symbols.extent_of(TELEMETRY_REGION)[0]
        header = inner.read_bytes(base, _TLM["NOVA_TLM_HEADER_SIZE"])
        if _u64(header, _TLM["NOVA_TLM_MAGIC_OFF"]) != _TLM["NOVA_TLM_MAGIC"]:
            raise NotPublishedYet("the firmware has not opened its region yet")
        version = _u32(header, _TLM["NOVA_TLM_VERSION_OFF"])
        if version != _TLM["NOVA_TLM_VERSION"]:
            raise ValueError(f"telemetry region version {version}, this reader speaks {_TLM['NOVA_TLM_VERSION']}")
        stride = _u32(header, _TLM["NOVA_TLM_DESCSIZE_OFF"])
        if stride != _TLM["NOVA_TLM_DESC_SIZE"]:
            raise ValueError(f"telemetry descriptor stride {stride}, this reader expects {_TLM['NOVA_TLM_DESC_SIZE']}")
        self.period_us = _u32(header, _TLM["NOVA_TLM_PERIOD_OFF"])
        # The manifest's rates were checked against the period read from
        # the source; this is the period the machine actually runs. An
        # image built before that constant changed would otherwise be
        # sampled by a manifest describing a different machine.
        declared = round(1_000_000 / PUBLISH_HZ)
        if self.period_us != declared:
            raise ValueError(
                f"telemetry period {self.period_us} us, the manifest was checked against {declared} us"
            )
        self.budget = _u32(header, _TLM["NOVA_TLM_BUDGET_OFF"])
        if not self.budget:
            raise ValueError("telemetry budget is zero; the publisher would never advance")
        self.bytes_published = _u32(header, _TLM["NOVA_TLM_BYTES_OFF"])
        # How old a published value can be. A turn copies until the
        # budget is spent, so a round takes as many turns as the payload
        # divides into it, and past one round every slot has been
        # revisited. Derived rather than written down: both terms move
        # with the firmware, and a bound stated in prose would not.
        turns_per_round = -(-self.bytes_published // self.budget)
        self.staleness_us = self.period_us * turns_per_round
        # Stated where it is derived, beside the publisher's own line,
        # so every path that opens a reader reports the same bound.
        print(f"[workbench] S: at most {self.staleness_us / 1000:g} ms stale")

        slots = _u32(header, _TLM["NOVA_TLM_SLOTS_OFF"])
        table = inner.read_bytes(base + _TLM["NOVA_TLM_HEADER_SIZE"], slots * stride)
        by_source: dict[int, int] = {}
        for index in range(slots):
            entry = table[index * stride : (index + 1) * stride]
            by_source[_u64(entry, _TLM["NOVA_TLM_DESC_SOURCE_OFF"])] = index
        self._descriptor = [base + _TLM["NOVA_TLM_HEADER_SIZE"] + index * stride for index in range(slots)]
        self._base = base

        # Want must be a subset of publish, checked here rather than
        # kept in agreement by hand. A symbol the firmware stopped
        # offering fails at attach, where it names itself, instead of
        # leaving one panel blank for a reason nothing states.
        self._slot: dict[str, int] = {}
        missing = []
        for obs in OBSERVATIONS:
            if obs.pa is not None:
                continue
            index = by_source.get(view.addresses[obs.symbol])
            if index is None:
                missing.append(obs.symbol)
            else:
                self._slot[obs.topic] = index
        if missing:
            raise ValueError("the firmware publishes no slot for: " + ", ".join(sorted(set(missing))))

    def read(self, obs: Obs, *, live: bool = True) -> object:
        # Guest memory, which the firmware does not publish and could
        # not vouch for if it did.
        if obs.pa is not None:
            return self._inner.read(obs)

        # Read the address itself. Safe only where nothing is writing —
        # a stopped machine, or a structure built once — because this
        # path has no sequence to check and cannot tell a torn value
        # from a whole one. What it adds over the published copy is
        # `staleness_us` worth of motion: a stopped machine takes no
        # further turn, so its last turn is as far as publication got.
        if not live:
            return self._inner.read(obs)

        index = self._slot[obs.topic]
        at = self._descriptor[index]
        descriptor = self._inner.read_bytes(at, _TLM["NOVA_TLM_DESC_SIZE"])
        before = _u64(descriptor, _TLM["NOVA_TLM_DESC_SEQ_OFF"])
        if before % 2:
            raise elfsym.TornRead(f"{obs.topic}: the publisher is inside the window")
        if self._seen.get(obs.topic) == before:
            raise Unchanged(obs.topic)

        offset = _u32(descriptor, _TLM["NOVA_TLM_DESC_AT_OFF"])
        size = _u32(descriptor, _TLM["NOVA_TLM_DESC_BYTES_OFF"])
        payload = self._inner.read_bytes(self._base + offset, size)
        # Re-read the sequence: a writer that crossed the copy leaves
        # bytes from two readings, which decode into a value the machine
        # never held.
        after = _u64(self._inner.read_bytes(at, _TLM["NOVA_TLM_DESC_SIZE"]), _TLM["NOVA_TLM_DESC_SEQ_OFF"])
        if after != before:
            raise elfsym.TornRead(f"{obs.topic}: the publisher crossed the read")

        self._seen[obs.topic] = before
        self.stamps[obs.topic] = _u64(descriptor, _TLM["NOVA_TLM_DESC_STAMP_OFF"])

        resolved = self._resolved[obs.topic]
        value = elfsym.decode(resolved.type, payload, fields=obs.fields)
        if obs.shape is not None:
            value = obs.shape(value, resolved.type)
        return _hexify(value) if obs.hex else value

    def read_bytes(self, pa: int, size: int) -> bytes:
        return self._inner.read_bytes(pa, size)

    @property
    def symbols(self) -> elfsym.SymbolTable:
        return self._symbols

    def close(self) -> None:
        self._inner.close()


class NotPublishedYet(Exception):
    """The firmware has not opened its region yet.

    Not a fault. EL2 opens the region during init, and the bridge starts
    polling as soon as the RAM backend exists, so on a slow enough host
    the first attempt lands ahead of it. Told apart from a real fault
    because the two want opposite things: this one waits, and a fault
    ends the run's S layer and says so.
    """


def open_provider(
    elf_path: Path, ram_path: Path, ram_base: int, view: observe.View
) -> SnapshotProvider:
    """The S reader for a live run.

    Published state where the firmware publishes it, raw memory
    underneath for what it does not: the page-table walk, which learns
    its addresses as it reads, and the guest's own pages. One name for
    the pair so callers compose nothing and the layering can change
    without them.
    """
    return TelemetryProvider(ElfRamProvider(elf_path, ram_path, ram_base, view), view)


class Unchanged(Exception):
    """The publisher says this value has not moved since it was last read.

    Distinct from reading it and finding it equal: nothing was decoded,
    because the firmware answered the question with one word. That is
    the whole point of the sequence — on a memory map it saves the
    decode, and on a link narrower than a memory map it is the
    difference between sending a value and not.
    """


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
            except (elfsym.TornRead, Unchanged):
                continue
            if self._last.get(obs.topic) != value:
                self._last[obs.topic] = value
                changes.append((obs, value))
        return changes

    def stamp(self, topic: str) -> int | None:
        """When the firmware says this topic last moved, on its own clock.

        None when the provider cannot say — a scripted or replayed one,
        or a reader of raw addresses, neither of which has a publisher
        to ask. A caller that cannot get a firmware clock falls back to
        the envelope's arrival time, which is what it had before.
        """

        return self._provider.stamps.get(topic)

    def sweep(self) -> list[tuple[Obs, object]]:
        """Every observation at once, rate and change gate ignored.

        For a machine that has stopped. A reader who has just arrived at
        an event wants the whole picture, including the fields that
        happen not to have changed since the last poll, which is the
        opposite of the delta the gate exists to find.

        `live=False` because a stopped machine takes no further turn:
        the published copy stays as old as its last one, where the
        address is the instant the machine stopped at. Direct reads are
        safe here for the same reason — nothing is writing.

        The cache is updated, so resuming does not replay as changes the
        values this already sent.
        """
        values = []
        for obs in self._observations:
            try:
                value = self._provider.read(obs, live=False)
            except elfsym.TornRead:
                continue
            self._last[obs.topic] = value
            values.append((obs, value))
        return values
