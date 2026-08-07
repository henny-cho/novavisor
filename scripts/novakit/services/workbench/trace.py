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

The recoverable depth is capacity - 1, not capacity: see
`_oldest_intact`. The difference only ever shows when the reader is
already behind.
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
        "NOVA_TRACE_TS_OFF",
        "NOVA_TRACE_MAX_RINGS",
    ],
)
MAGIC = _LAYOUT["NOVA_TRACE_MAGIC"]
VERSION = _LAYOUT["NOVA_TRACE_VERSION"]
MAX_RINGS = _LAYOUT["NOVA_TRACE_MAX_RINGS"]
# Public: anything keeping records keeps them at the firmware's width,
# so the layout stays one number rather than one per holder.
REC_SIZE = _LAYOUT["NOVA_TRACE_REC_SIZE"]
_HEADER_SIZE = _LAYOUT["NOVA_TRACE_HEADER_SIZE"]
_HEAD_OFF = _LAYOUT["NOVA_TRACE_HEAD_OFF"]
_RECORDS_OFF = _LAYOUT["NOVA_TRACE_RECORDS_OFF"]

# The code this reader writes where records should have been. Taken
# from the catalogue rather than read out of the header a second time:
# one entry names the moment, its number, and what its words mean.
GAP_CODE = events.BY_ID["trace.gap"].code
# `a` is a u32 on the wire. A loss past four billion events is a
# saturated count rather than a wrapped one.
_MAX_COUNT = 0xFFFF_FFFF

# Region header, then one record. Both are fixed by the ABI header the
# firmware compiles against; the struct strings only spell its fields.
_HEADER = struct.Struct("<QIIIIIII")
_RECORD = struct.Struct("<QHBBIQQ")
_TS = struct.Struct("<Q")
_TS_OFF = _LAYOUT["NOVA_TRACE_TS_OFF"]


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


def pack_into(buffer, offset: int, record: Record) -> None:
    """Write a record back in the firmware's own layout.

    Anything holding many records holds them like this. As Record
    objects the same count costs several times the bytes, and a holder
    that decoded on the way in would pay for every record to answer
    about the few a reader asks for.
    """
    _RECORD.pack_into(
        buffer, offset, record.ts, record.code, record.cpu, 0, record.a, record.b, record.c
    )


def timestamp_at(buffer, offset: int) -> int:
    """A stored record's timestamp, without decoding the rest of it.

    Eight bytes at the offset the layout already fixes — read from the
    ABI header like every other offset here, so nothing has to remember
    that ts happens to come first. A holder that mirrored the timestamps
    into an array of its own would have a second thing to keep true on
    every write, and the first edit that forgot would search the wrong
    order over records that are all still there.
    """
    return _TS.unpack_from(buffer, offset + _TS_OFF)[0]


def unpack_from(buffer, offset: int) -> Record:
    ts, code, cpu, _flags, a, b, c = _RECORD.unpack_from(buffer, offset)
    return Record(ts, code, cpu, a, b, c)


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


class Budget:
    """What the ring's depth is worth, in the terms that decide it.

    Capacity is a latency budget: how long the host may go without
    emptying a ring before the firmware laps it. Both sides of that are
    runtime facts — how fast the busiest ring fills, and how long this
    host actually goes between looks — and neither was ever measured.
    The depth was picked from one afternoon's numbers on one laptop,
    which is a number that stops being true on the next machine and
    says nothing when it does.

    So the instrument states its own terms, and the moment the observed
    stall passes the horizon those terms promise, that crossing is
    itself something to observe rather than something to rediscover by
    hand next time the marks look wrong.
    """

    def __init__(self, capacity: int):
        self.capacity = capacity
        self._at: float | None = None
        self._peak = 0.0  # records per second into one ring
        self._worst = 0.0  # seconds between looks

    def looked(self, records: list[Record], at: float) -> None:
        """One opportunity to drain, and what it found.

        The interval is between *looks*, not between drains that found
        something. A ring that was empty was never at risk, so counting
        an idle stretch as a stall would turn the worst case into a
        measure of how quiet the run was.

        The rate is per ring, because the depth is per ring: `cpu` is
        which ring wrote the record, and a total across four of them
        would claim a horizon four times shorter than the real one.
        """
        if self._at is not None:
            interval = at - self._at
            self._worst = max(self._worst, interval)
            if interval > 0 and records:
                per_ring: dict[int, int] = {}
                for record in records:
                    if record.code != GAP_CODE:
                        per_ring[record.cpu] = per_ring.get(record.cpu, 0) + 1
                if per_ring:
                    self._peak = max(self._peak, max(per_ring.values()) / interval)
        self._at = at

    @property
    def horizon_seconds(self) -> float:
        """How long the ring covers at the fastest fill yet seen.

        Zero until something has been seen. An unmeasured budget is not
        an unlimited one, and reporting it as zero rather than as
        infinity is the difference between the two.
        """
        return self.capacity / self._peak if self._peak else 0.0

    @property
    def overrun(self) -> bool:
        """Whether this run has already gone longer without looking than
        the ring can cover. Not a prediction — a thing that happened."""
        return bool(self._peak) and self._worst > self.horizon_seconds

    def as_dict(self) -> dict:
        return {
            "capacity": self.capacity,
            "peak_rate": round(self._peak),
            "worst_gap_ms": round(self._worst * 1000, 1),
            "horizon_ms": round(self.horizon_seconds * 1000, 1),
            "overrun": self.overrun,
        }


class TraceReader:
    """One run's rings, read from the shared RAM file.

    `ram_base` is where the machine's RAM aperture starts, so a physical
    address is read at `pa - ram_base` — the same one constant the S
    layer's provider uses, and the only address arithmetic here.

    `region_size` comes from the board too. It is how much a board chose
    to spend on the T layer, and the depth of every ring follows from
    it, so a copy kept here would be this reader's opinion of another
    machine's memory map.
    """

    def __init__(self, ram_path: Path, ram_base: int, trace_pa: int, region_size: int):
        self._offset = trace_pa - ram_base
        self._region_size = region_size
        with Path(ram_path).open("rb") as backing:
            self._ram = mmap.mmap(backing.fileno(), 0, prot=mmap.PROT_READ)
        try:
            if len(self._ram) < self._offset + region_size:
                # QEMU sizes the backend as it comes up, so a short file
                # is a launch in progress, not a machine without room.
                raise NotYetFormatted("RAM backend is smaller than the trace region")
            self.geometry = self._read_geometry()
        except BaseException:
            self._ram.close()
            raise
        self._cursor = [0] * self.geometry.rings
        # The last timestamp handed out per ring, which is where the
        # next hole opens, and what a drain that recovered nothing has
        # to carry until a record gives it somewhere to end.
        self._last_ts = [0] * self.geometry.rings
        self._pending = [0] * self.geometry.rings
        # The pre-placement drops, waiting for a timestamp to sit at.
        self._early_pending = self.geometry.early

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
        if record_size != REC_SIZE:
            raise NotFormatted(f"trace record is {record_size} bytes, expected {REC_SIZE}")
        # The depth is the region divided by the ring count, so it
        # arrives here and nowhere else — there is no constant left to
        # check it against. That makes vetting it this reader's job:
        # every number below is one it is about to index with.
        if not 1 <= rings <= MAX_RINGS:
            raise NotFormatted(f"trace region declares {rings} rings, expected 1..{MAX_RINGS}")
        if capacity < 1 or capacity & (capacity - 1):
            raise NotFormatted(f"trace ring capacity {capacity} is not a power of two")
        if stride != _RECORDS_OFF + capacity * REC_SIZE:
            raise NotFormatted(f"trace stride {stride} disagrees with capacity {capacity}")
        if _HEADER_SIZE + rings * stride > self._region_size:
            raise NotFormatted(f"{rings} rings of {capacity} do not fit {self._region_size} bytes")
        return Geometry(rings, capacity, stride, freq, early)

    def _record(self, at: int) -> Record:
        # The reserved byte is unpacked and dropped: it belongs to the
        # layout, not to the event, and carrying it would put a field
        # with no meaning on the wire.
        return unpack_from(self._ram, at)

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

    def drain(self) -> list[Record]:
        """Everything written since the last call, oldest first, and a
        record for everything that was not.

        Ordered across rings by timestamp. CNTPCT is common to every PE,
        so that ordering is the machine's real one — which is the thing
        a sampled layer can never supply, whatever its rate.

        A hole comes back as a record rather than as a count beside the
        list. The count was the honest number and the wrong shape: this
        function knows both ends of every hole, and a caller handed an
        integer can only say that something was lost, never that it was
        lost *there*. Two marks with eight thousand records missing
        between them are drawn as neighbours, and the causal chain a
        reader takes from that is fiction.
        """
        found: list[Record] = []
        for ring in range(self.geometry.rings):
            found += self._drain_one(ring)
        found.sort(key=lambda record: record.ts)
        return self._with_early(found)

    def _with_early(self, records: list[Record]) -> list[Record]:
        """Fold the pre-placement drops in, once, at the front.

        A different loss from a lapped ring — these predate the region,
        so no drain however prompt could have caught them — but the same
        hole in the run, and the boot they fall in is where a reader is
        most likely to be looking for something. `b` is zero: nothing
        precedes them, so the hole has no near end.
        """
        if not self._early_pending or not records:
            return records
        early, self._early_pending = self._early_pending, 0
        records.insert(
            0, Record(ts=records[0].ts, code=GAP_CODE, cpu=0, a=min(early, _MAX_COUNT), b=0, c=0)
        )
        return records

    def _oldest_intact(self, head: int) -> int:
        """The oldest index still whole when the writer's head reads
        `head`.

        One short of the capacity, and that is not caution. Head at H
        means the writer has published H records and is *inside* the
        slot for index H — which is the slot index H - capacity
        occupies. That record is already being destroyed, so the
        recoverable depth is capacity - 1, and a reader that kept the
        last `capacity` would hand out one record built from two
        events. It costs nothing while the reader keeps up: the cursor
        is newer than this bound, so the bound never applies.
        """
        capacity = self.geometry.capacity
        return head - capacity + 1 if head >= capacity else 0

    def _drain_one(self, ring: int) -> list[Record]:
        capacity = self.geometry.capacity
        base = self._offset + _HEADER_SIZE + ring * self.geometry.stride + _RECORDS_OFF
        cursor = self._cursor[ring]
        head = self._head(ring)
        if head < cursor:
            # The region was re-formatted under us (a restart that kept
            # the same backing file). Start again rather than report a
            # loss of minus several thousand.
            cursor = 0
        keep = max(cursor, self._oldest_intact(head))
        records = [
            self._record(base + (index % capacity) * REC_SIZE)
            for index in range(keep, head)
        ]
        # Re-read: anything the writer lapped while we were copying has
        # fallen out of the window, so it is discarded rather than
        # trusted. Reading a record that is being written is only
        # possible through exactly this race.
        safe = self._oldest_intact(self._head(ring))
        if safe > keep:
            del records[: min(safe - keep, len(records))]
        self._cursor[ring] = head

        missed = (head - cursor) - len(records) + self._pending[ring]
        if not records:
            # Nothing survived, so there is no timestamp to close a hole
            # on. Carried rather than placed at a moment nothing
            # happened: the count is never dropped, only deferred to the
            # drain that can say where it ends.
            self._pending[ring] = missed
            return records
        self._pending[ring] = 0
        # Both ends, read before the cursor moves past them: the hole
        # opened at the last record this ring handed out and closes at
        # the first that survived it.
        opened, closed = self._last_ts[ring], records[0].ts
        self._last_ts[ring] = records[-1].ts
        if missed:
            records.insert(
                0,
                Record(ts=closed, code=GAP_CODE, cpu=ring, a=min(missed, _MAX_COUNT),
                       b=opened, c=0),
            )
        return records

    def close(self) -> None:
        self._ram.close()


def dropped_in(records: list[Record]) -> int:
    """Events the gap records in this batch account for.

    The one place the number is computed. A badge showing a count and a
    window drawing the holes have to be two views of one drain, not two
    accounts of it — so the summary derives its total from the same
    records the window will hand out.
    """
    return sum(record.a for record in records if record.code == GAP_CODE)


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
    return {"edges": edges, "last": last, "dropped": dropped_in(records)}


def histogram(records: list[Record], first: int, last: int, buckets: int) -> dict[str, list[int]]:
    """How many of each event fell in each column of a window.

    Always the whole window. A wide request answered with the first N
    records and a count of the rest is honest arithmetic about a
    question nobody asked: a reader who dragged the window out wants to
    know what happened and how much of it, and a 1200-pixel strip could
    not draw fifty thousand separate marks anyway.

    Keyed by event rather than by path. Three events share the `post`
    edge, and a lane per path would sum them into one column that no
    longer says which fired; the UI has the catalogue and can group.
    """
    span = max(1, last - first + 1)
    out: dict[str, list[int]] = {}
    for record in records:
        entry = events.BY_CODE.get(record.code)
        if entry is None:
            continue
        column = min(buckets - 1, (record.ts - first) * buckets // span)
        lane = out.get(entry.id)
        if lane is None:
            lane = out[entry.id] = [0] * buckets
        lane[max(0, column)] += 1
    return out


def columns(records: list[Record], first: int) -> dict[str, list[int]]:
    """Records as parallel arrays, timestamps relative to the window.

    Repeating six field names per record costs ~110 bytes against ~40
    for the columns, and the browser's decode is an indexed loop either
    way. Relative timestamps keep the numbers small and well inside the
    range a JSON number carries exactly, which a raw 64-bit counter is
    not guaranteed to be.
    """
    return {
        "ts": [record.ts - first for record in records],
        "code": [record.code for record in records],
        "cpu": [record.cpu for record in records],
        "a": [record.a for record in records],
        "b": [record.b for record in records],
        "c": [record.c for record in records],
    }


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
    elif entry.id == "gic.ack":
        out |= {"intid": record.a}
    elif entry.id == "smp.cross":
        out |= {"vm": record.a, "owner": record.b}
    elif entry.id == "ivc.doorbell":
        out |= {"vm": record.a, "vintid": record.b}
    elif entry.id == "psci.call":
        out |= {"func": f"{record.a:#x}", "arg": f"{record.b:#x}", "action": record.c}
    elif entry.id == "uart.line":
        out |= {"slot": record.a, "bytes": record.b}
    elif entry.id == "smmu.fault":
        out |= {"stream": record.a, "vm": record.b, "generation": record.c}
    elif entry.id == "trace.gap":
        # The width, not the far end: `b` is a raw counter value, which
        # is the one thing no reader can use. Zero when the hole opened
        # before anything was recorded, so it has no measurable start.
        out |= {"count": record.a, "ticks": record.ts - record.b if record.b else 0}
    return out


def report(shm_path: Path, ram_base: int, trace_pa: int, region_size: int, limit: int = 40) -> int:
    """The terminal twin of the T layer, read off the rings themselves.

    The fallback path: no bridge, so no history, and what arrives here
    is only what the rings still hold — however deep the board's region
    divided into. Needs no browser and no image either, which is what
    makes it the answer when there is nothing else running.
    """
    try:
        reader = TraceReader(shm_path, ram_base, trace_pa, region_size)
    except (NotFormatted, OSError) as error:
        print(f"[workbench] trace: {error}", file=sys.stderr)
        return 1
    try:
        records = reader.drain()
        geometry = reader.geometry
    finally:
        reader.close()

    lost = dropped_in(records)
    print(
        f"rings {geometry.rings}  capacity {geometry.capacity}  "
        f"cntfrq {geometry.freq_hz}  records {len(records)}  dropped {lost}  "
        f"early {geometry.early}"
    )
    print_records(records, geometry.freq_hz, limit)
    return 0


def print_records(records: list[Record], freq_hz: int, limit: int) -> None:
    """Ordered records as lines, newest `limit` of them.

    One printer for both sources. The rings and the bridge's history
    hold the same records at the same width, and two renderings of one
    thing is how they come to disagree about what a run looked like.
    """
    counts: dict[str, int] = {}
    for record in records:
        counts[record.event or str(record.code)] = counts.get(record.event or str(record.code), 0) + 1
    if counts:
        print("  " + "  ".join(f"{name}={count}" for name, count in sorted(counts.items())))
    shown = records[-limit:]
    base = shown[0].ts if shown else 0
    for record in shown:
        fields = decode(record)
        # A field named `ticks` is a duration in the same clock as the
        # stamp, and printed raw it is a counter value nobody can read.
        # One rule rather than one case per event that has one.
        if "ticks" in fields:
            fields["ticks"] = f"{_micros(fields['ticks'], freq_hz)}us"
        detail = " ".join(
            f"{key}={value}" for key, value in fields.items() if key not in ("event", "cpu", "ts")
        )
        # Relative to the first record shown: absolute counter values
        # say nothing a reader can use, and the frequency is right here.
        print(
            f"  {_micros(record.ts - base, freq_hz):>10}us "
            f"cpu{record.cpu} {fields['event']:<13} {detail}"
        )


def _micros(ticks: int, freq_hz: int) -> int:
    return ticks * 1_000_000 // max(1, freq_hz)
