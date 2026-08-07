"""The T layer: draining the firmware's event rings.

Independent of the S layer by construction. This needs the board's
reserved address and nothing else — no ELF, no `.symtab`, no DWARF — so
symbol resolution failing does not take the event stream with it, and a
stripped release image is still readable.

The reader owns no index in the region. It keeps its cursor here, in the
host's own memory, and works out what it missed:

    lost = (head - cursor) - records actually recovered

Derived rather than accumulated. Counting the pre-copy and post-copy
skips separately double-counts wherever a lapping writer makes the two
overlap — a mistake the firmware-side test made first.
"""

from __future__ import annotations

import mmap
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

from ...image import abi
from . import events

_LAYOUT = abi.read_defines(
    abi.TRACE_RING,
    [
        "NOVA_TRACE_MAGIC",
        "NOVA_TRACE_VERSION",
        "NOVA_TRACE_HEADER_SIZE",
        "NOVA_TRACE_HEAD_OFF",
        "NOVA_TRACE_RECORDS_OFF",
        "NOVA_TRACE_REC_SIZE",
        "NOVA_TRACE_SIZE",
    ],
)
MAGIC = _LAYOUT["NOVA_TRACE_MAGIC"]
VERSION = _LAYOUT["NOVA_TRACE_VERSION"]
REGION_SIZE = _LAYOUT["NOVA_TRACE_SIZE"]
_HEADER_SIZE = _LAYOUT["NOVA_TRACE_HEADER_SIZE"]
_HEAD_OFF = _LAYOUT["NOVA_TRACE_HEAD_OFF"]
_RECORDS_OFF = _LAYOUT["NOVA_TRACE_RECORDS_OFF"]
_REC_SIZE = _LAYOUT["NOVA_TRACE_REC_SIZE"]

# Region header, then one record. Both are fixed by the ABI header the
# firmware compiles against; the struct strings only spell its fields.
_HEADER = struct.Struct("<QIIIIIII")
_RECORD = struct.Struct("<QHBBIQQ")


class NotFormatted(RuntimeError):
    """The region carries no ring this reader understands.

    Raised rather than papered over: a wrong version or geometry means
    the firmware and this file disagree about the layout, and decoding
    anyway would turn that into plausible-looking events.
    """


class NotYetFormatted(NotFormatted):
    """Nothing has been placed here — so far.

    EL2 formats the region in its first init action, which is later
    than QEMU creating and sizing the backing file, so an empty region
    right after launch is a moment in a launch rather than a fact about
    an image. Distinct from its parent because the two call for
    opposite responses: this one is answered by asking again, and a
    version disagreement never will be.
    """


# The image symbol that settles whether a build carries the ring
# writer. One-sided: present means certainly yes, absent only means
# this reader cannot tell, since an optimised image inlines every use
# and keeps no name.
WRITER_SYMBOL = "nova::trace::g_ring"


@dataclass(frozen=True)
class Record:
    ts: int
    code: int
    cpu: int
    a: int
    b: int
    c: int

    @property
    def event(self) -> str:
        entry = events.BY_CODE.get(self.code)
        return entry.id if entry else ""

    @property
    def edge(self) -> str:
        entry = events.BY_CODE.get(self.code)
        return entry.edge if entry else ""


@dataclass(frozen=True)
class Geometry:
    rings: int
    capacity: int
    stride: int
    freq_hz: int
    # Events the firmware emitted before it had a ring to put them in.
    # Not a drain loss — it happened before this reader could exist —
    # but the one part of the run no cursor arithmetic can recover, so
    # it travels with the geometry rather than going unmentioned.
    early: int = 0


class TraceReader:
    """One run's rings, read from the shared RAM file.

    `ram_base` is where the machine's RAM aperture starts, so a physical
    address is read at `pa - ram_base` — the same one constant the S
    layer's provider uses, and the only address arithmetic here.
    """

    def __init__(self, ram_path: Path, ram_base: int, trace_pa: int):
        self._offset = trace_pa - ram_base
        with Path(ram_path).open("rb") as backing:
            self._ram = mmap.mmap(backing.fileno(), 0, prot=mmap.PROT_READ)
        try:
            if len(self._ram) < self._offset + REGION_SIZE:
                # QEMU sizes the backend as it comes up, so a short file
                # is a launch in progress, not a machine without room.
                raise NotYetFormatted("RAM backend is smaller than the trace region")
            self.geometry = self._read_geometry()
        except BaseException:
            self._ram.close()
            raise
        self._cursor = [0] * self.geometry.rings

    def _read_geometry(self) -> Geometry:
        magic, version, record_size, stride, rings, capacity, freq, early = _HEADER.unpack_from(
            self._ram, self._offset
        )
        if magic != MAGIC:
            # The magic is written last, so its absence says nothing
            # about the layout — only that nobody has finished placing
            # one here. Stale bytes from a previous boot read the same
            # way, and mean the same thing for this run.
            raise NotYetFormatted(f"no trace region at {self._offset:#x} (magic {magic:#x})")
        if version != VERSION:
            raise NotFormatted(f"trace region version {version}, expected {VERSION}")
        if record_size != _REC_SIZE:
            raise NotFormatted(f"trace record is {record_size} bytes, expected {_REC_SIZE}")
        if rings < 1 or capacity < 1 or capacity & (capacity - 1):
            raise NotFormatted(f"trace geometry is not usable: {rings} rings, capacity {capacity}")
        return Geometry(rings, capacity, stride, freq, early)

    def _record(self, at: int) -> Record:
        # The reserved byte is unpacked and dropped: it belongs to the
        # layout, not to the event, and carrying it would put a field
        # with no meaning on the wire.
        ts, code, cpu, _flags, a, b, c = _RECORD.unpack_from(self._ram, at)
        return Record(ts, code, cpu, a, b, c)

    def _head(self, ring: int) -> int:
        base = self._offset + _HEADER_SIZE + ring * self.geometry.stride
        return int.from_bytes(self._ram[base + _HEAD_OFF : base + _HEAD_OFF + 8], "little")

    def pending(self) -> int:
        """Records waiting across every ring.

        Two eight-byte reads and no decode, so a caller can ask far more
        often than it can afford to drain — which is what lets an idle
        tick skip the work rather than budget for it.
        """
        return sum(
            max(0, self._head(ring) - self._cursor[ring])
            for ring in range(self.geometry.rings)
        )

    def drain(self) -> tuple[list[Record], int]:
        """Everything written since the last call, oldest first.

        Ordered across rings by timestamp. CNTPCT is common to every PE,
        so that ordering is the machine's real one — which is the thing
        a sampled layer can never supply, whatever its rate.
        """
        found: list[Record] = []
        lost = 0
        for ring in range(self.geometry.rings):
            records, missed = self._drain_one(ring)
            found += records
            lost += missed
        found.sort(key=lambda record: record.ts)
        return found, lost

    def _drain_one(self, ring: int) -> tuple[list[Record], int]:
        capacity = self.geometry.capacity
        base = self._offset + _HEADER_SIZE + ring * self.geometry.stride + _RECORDS_OFF
        cursor = self._cursor[ring]
        head = self._head(ring)
        if head < cursor:
            # The region was re-formatted under us (a restart that kept
            # the same backing file). Start again rather than report a
            # loss of minus several thousand.
            cursor = 0
        oldest = max(0, head - capacity)
        records = [
            self._record(base + (index % capacity) * _REC_SIZE)
            for index in range(max(cursor, oldest), head)
        ]
        # Re-read: anything the writer lapped while we were copying has
        # fallen out of the window, so it is discarded rather than
        # trusted. Reading a record that is being written is only
        # possible through exactly this race.
        safe = max(0, self._head(ring) - capacity)
        keep = max(cursor, oldest)
        if safe > keep:
            del records[: min(safe - keep, len(records))]
        self._cursor[ring] = head
        return records, (head - cursor) - len(records)

    def close(self) -> None:
        self._ram.close()


def summarise(records: list[Record]) -> dict:
    """What crosses the wire: counts per path, and the last of each.

    The records themselves stay here. A few thousand events a second is
    nothing to a bridge and a great deal to a browser, and a cap with a
    silent drop would make "everything that happened" a lie. The board
    needs to know which paths fired and how often; a reader who wants
    the events themselves asks for them.
    """
    edges: dict[str, int] = {}
    last: dict[str, dict] = {}
    for record in records:
        entry = events.BY_CODE.get(record.code)
        if entry is None or not entry.edge:
            continue
        edges[entry.edge] = edges.get(entry.edge, 0) + 1
        last[entry.edge] = decode(record)
    return {"edges": edges, "last": last}


def decode(record: Record) -> dict:
    """A record as the event it is, with its arguments named.

    The firmware packs the two INTIDs of a binding into one word — high
    half physical — because that pairing is the whole content of the
    event. Splitting it here rather than in the UI keeps the packing a
    detail of the wire between EL2 and this file.
    """
    entry = events.BY_CODE.get(record.code)
    out: dict = {"event": entry.id if entry else str(record.code), "cpu": record.cpu, "ts": record.ts}
    if entry is None:
        return out
    if entry.id == "vgic.bind":
        out |= {
            "vm": record.a,
            "vintid": record.b & 0xFFFF_FFFF,
            "pintid": record.b >> 32,
            "generation": record.c,
        }
    elif entry.id == "vgic.eoi":
        out |= {
            "slot": record.a,
            "vintid": record.b & 0xFFFF_FFFF,
            "pintid": record.b >> 32,
            "generation": record.c,
        }
    elif entry.id == "vgic.inject":
        out |= {"slot": record.a, "vintid": record.b & 0xFFFF_FFFF, "lr": record.b >> 32,
                "generation": record.c}
    elif entry.id in ("vgic.spi", "vgic.private"):
        out |= {"vm" if entry.id == "vgic.spi" else "slot": record.a, "vintid": record.b}
    elif entry.id == "trap":
        out |= {"ec": record.a, "esr": f"{record.b:#x}", "far": f"{record.c:#x}"}
    elif entry.id == "mmio":
        out |= {"size": record.a & 0xFF, "write": bool(record.a & 0x100),
                "ipa": f"{record.b:#x}", "value": f"{record.c:#x}"}
    elif entry.id == "sched.switch":
        out |= {"next": record.a, "prev": record.b}
    return out


def report(shm_path: Path, ram_base: int, trace_pa: int, limit: int = 40) -> int:
    """The terminal twin of the T layer: what the firmware recorded.

    The same reader the bridge uses, against a live session's surface —
    no browser, and no image either. What arrives here is everything the
    rings hold; the wire only ever carries the summary.
    """
    try:
        reader = TraceReader(shm_path, ram_base, trace_pa)
    except (NotFormatted, OSError) as error:
        print(f"[workbench] trace: {error}", file=sys.stderr)
        return 1
    try:
        records, lost = reader.drain()
        geometry = reader.geometry
    finally:
        reader.close()

    print(
        f"rings {geometry.rings}  capacity {geometry.capacity}  "
        f"cntfrq {geometry.freq_hz}  records {len(records)}  dropped {lost}  "
        f"early {geometry.early}"
    )
    if lost:
        # Never silently: a ring that lapped is a fact about the run,
        # and a report that hid it would read as a complete history.
        print(f"[workbench] {lost} record(s) overwritten before this drain", file=sys.stderr)
    if geometry.early:
        # A different fact from `dropped`, and not actionable the same
        # way: these predate the region, so no drain could have caught
        # them however prompt it was.
        print(
            f"[workbench] {geometry.early} event(s) emitted before the rings were placed",
            file=sys.stderr,
        )
    counts: dict[str, int] = {}
    for record in records:
        counts[record.event or str(record.code)] = counts.get(record.event or str(record.code), 0) + 1
    if counts:
        print("  " + "  ".join(f"{name}={count}" for name, count in sorted(counts.items())))
    base = records[0].ts if records else 0
    for record in records[-limit:]:
        fields = decode(record)
        detail = " ".join(
            f"{key}={value}" for key, value in fields.items() if key not in ("event", "cpu", "ts")
        )
        # Relative to the first record shown: absolute counter values
        # say nothing a reader can use, and the frequency is right here.
        micros = (record.ts - base) * 1_000_000 // max(1, geometry.freq_hz)
        print(f"  {micros:>10}us cpu{record.cpu} {fields['event']:<13} {detail}")
    return 0
