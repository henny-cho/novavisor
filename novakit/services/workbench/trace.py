"""The T layer: draining the firmware's event rings.

Independent of the S layer by construction. This needs the board's
reserved address and nothing else — no ELF, no `.symtab`, no DWARF — so
symbol resolution failing does not take the event stream with it, and a
stripped release image is still readable.

The reader owns no index in the region. It keeps its cursor in the
host's own memory and derives what it missed:

    lost = (head - cursor) - records actually recovered

Derived rather than accumulated: counting the pre-copy and post-copy
skips separately double-counts wherever a lapping writer makes the two
overlap.

The recoverable depth is capacity - 1, not capacity; see
`_oldest_intact`. The difference only shows when the reader is behind.
"""

from __future__ import annotations

import gc
import mmap
import struct
import sys
import time
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path

from ...image import abi
from . import commands, events

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
        "NOVA_TRACE_HORIZON_MS",
    ],
)
MAGIC = _LAYOUT["NOVA_TRACE_MAGIC"]
VERSION = _LAYOUT["NOVA_TRACE_VERSION"]
MAX_RINGS = _LAYOUT["NOVA_TRACE_MAX_RINGS"]
# Public: everything that keeps records keeps them at the firmware's
# width, so the layout stays one number rather than one per holder.
REC_SIZE = _LAYOUT["NOVA_TRACE_REC_SIZE"]
_HEADER_SIZE = _LAYOUT["NOVA_TRACE_HEADER_SIZE"]
_HEAD_OFF = _LAYOUT["NOVA_TRACE_HEAD_OFF"]
_RECORDS_OFF = _LAYOUT["NOVA_TRACE_RECORDS_OFF"]

# The code this reader writes where records should have been. Taken from
# the catalogue rather than the ABI header a second time: one entry names
# the moment, its number, and what its words mean.
GAP_CODE = events.BY_ID["trace.gap"].code

# The window a fill rate is measured over, taken from the same header
# that declares the rate a board is sized against. A rate has no meaning
# without one, and measuring over a different span than the declaration
# makes the two incomparable — the densest microsecond of a boot is
# hundreds of times the busiest second, and sizing a ring against it
# would reserve for a burst no depth ever has to survive.
HORIZON_SECONDS = _LAYOUT["NOVA_TRACE_HORIZON_MS"] / 1000
# How finely the window slides. Ten puts a burst's boundary within a
# tenth of the window instead of splitting it across two of them.
_RATE_SLICES = 10
# `a` is a u32 on the wire, so a loss past four billion events saturates
# rather than wraps.
_MAX_COUNT = 0xFFFF_FFFF

# Region header, then one record. Both fixed by the ABI header the
# firmware compiles against; these strings only spell its fields.
_HEADER = struct.Struct("<QIIIIIII")
_RECORD = struct.Struct("<QHBBIQQ")
_TS = struct.Struct("<Q")
_TS_OFF = _LAYOUT["NOVA_TRACE_TS_OFF"]
# The spelling above and the size the ABI declares are two statements of
# one layout, and untied they part silently — a field added to the
# record would leave this reading each slot at the wrong stride and
# decoding neighbours as events.
if _RECORD.size != REC_SIZE:
    raise SystemExit(f"trace record is {REC_SIZE} bytes; this reads {_RECORD.size}")


def stride_for(capacity: int) -> int:
    """How far apart two rings sit: one head, then that many records.

    Stated once. The reader holds a region's own stride to this, and
    whoever lays a region out derives it from the same place, so the two
    cannot arrive at different numbers for one depth.
    """
    return _RECORDS_OFF + capacity * REC_SIZE


def format_region(
    buffer,
    offset: int,
    *,
    rings: int,
    capacity: int,
    freq_hz: int,
    early: int = 0,
    magic: int | None = None,
    version: int | None = None,
    stride: int | None = None,
    header_rings: int | None = None,
) -> None:
    """Write a region header the way the firmware writes one.

    Beside the reader that consumes it, because the two are one layout:
    a caller spelling the struct out itself is a copy that stays right
    exactly until a field moves, and then decodes neighbours as geometry.

    The overrides are how a caller writes a header the firmware could not
    have — a wrong magic, a stride that disagrees with the capacity —
    which is what the reader's vetting is for. Left alone the fields stay
    consistent with each other.
    """
    _HEADER.pack_into(
        buffer,
        offset,
        MAGIC if magic is None else magic,
        VERSION if version is None else version,
        REC_SIZE,
        stride_for(capacity) if stride is None else stride,
        rings if header_rings is None else header_rings,
        capacity,
        freq_hz,
        early,
    )


class NotFormatted(RuntimeError):
    """The region carries no ring this reader understands.

    Raised rather than papered over: a wrong version or geometry means
    the firmware and this file disagree about the layout, and decoding
    anyway would turn that into plausible-looking events.
    """


class NotYetFormatted(NotFormatted):
    """Nothing has been placed here yet.

    EL2 formats the region in its first init action, later than QEMU
    creating and sizing the backing file, so an empty region right after
    launch is a moment in a launch rather than a fact about an image.
    Distinct from its parent because this one is answered by asking
    again and a version disagreement never will be.
    """


# The image symbol that settles whether a build carries the ring writer.
# One-sided: present means certainly yes, absent only means this reader
# cannot tell, since an optimised image inlines every use.
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

    Everything holding many records holds them like this. As Record
    objects the same count costs several times the bytes, and a holder
    that decoded on the way in would pay for every record to answer
    about the few a reader asks for.
    """
    _RECORD.pack_into(
        buffer, offset, record.ts, record.code, record.cpu, 0, record.a, record.b, record.c
    )


def timestamp_at(buffer, offset: int) -> int:
    """A stored record's timestamp, without decoding the rest of it.

    Eight bytes at the offset the ABI header fixes, read like every
    other offset here, so nothing has to remember that ts comes first.
    """
    return _TS.unpack_from(buffer, offset + _TS_OFF)[0]


def unpack_from(buffer, offset: int) -> Record:
    ts, code, cpu, _flags, a, b, c = _RECORD.unpack_from(buffer, offset)
    return Record(ts, code, cpu, a, b, c)


def unpack_all(packed: bytes) -> list[Record]:
    """Every record in a packed run, in the order it was written."""
    return [
        Record(ts, code, cpu, a, b, c)
        for ts, code, cpu, _flags, a, b, c in _RECORD.iter_unpack(packed)
    ]


@dataclass(frozen=True)
class Geometry:
    rings: int
    capacity: int
    stride: int
    freq_hz: int
    # Events emitted before the region was placed. Not a drain loss — it
    # predates this reader — but the one part of the run no cursor
    # arithmetic can recover, so it travels with the geometry.
    early: int = 0


class _GcClock:
    """How long this process has spent inside garbage collection.

    CPython reports collections, not their cost, so the pause is timed
    from the interpreter's own start/stop callbacks. The callback list
    is process-wide, so one instance is installed for the process and
    never removed — a bridge that stopped counting halfway would make
    the attribution of a later stall depend on when it was asked.
    """

    def __init__(self) -> None:
        self.total = 0.0
        self._entered: float | None = None

    def __call__(self, phase: str, _info: dict) -> None:
        if phase == "start":
            self._entered = time.perf_counter()
        elif self._entered is not None:
            self.total += time.perf_counter() - self._entered
            self._entered = None


def _gc_clock() -> float:
    global _GC
    if _GC is None:
        _GC = _GcClock()
        gc.callbacks.append(_GC)
    return _GC.total


_GC: _GcClock | None = None


def _gap_bucket(interval: float) -> int:
    """Which power-of-two millisecond band an interval falls in.

    Buckets appear where there is data instead of being declared, so no
    table of edges travels with the histogram and none has to be revised
    when the loop's cadence or the ring's depth changes. Band 0 holds
    everything under a millisecond — the loop's own tick lives there and
    is the denominator the outliers are read against.

    The bands are plain durations on purpose. Judging them against the
    horizon needs the horizon, which is published beside them and moves
    as the peak fill grows: binning by that ratio here would freeze each
    look against whatever the peak happened to be at the time.
    """
    ms = interval * 1000
    return 1 << int(ms).bit_length() - 1 if ms >= 1 else 0


class Budget:
    """What the ring's depth is worth on this host, measured.

    Capacity is a latency budget: how long the host may go without
    emptying a ring before the firmware laps it. Both terms are runtime
    facts — how fast the busiest ring fills, and how long this host
    actually goes between looks — so the instrument measures its own
    rather than inheriting the figures a board was sized against.
    """

    def __init__(
        self, capacity: int, freq_hz: int, cpu_clock=time.process_time, gc_clock=_gc_clock
    ):
        self.capacity = capacity
        self._freq = freq_hz
        self._window = int(freq_hz * HORIZON_SECONDS)
        self._slice = max(1, self._window // _RATE_SLICES)
        self._slices: dict[int, deque[int]] = {}  # ring -> counts, newest last
        self._opened: dict[int, int] = {}  # ring -> stamp the last slice began at
        self._at: float | None = None
        self._peak = 0.0  # records per second into one ring
        self._worst = 0.0  # seconds between looks
        self._gaps: dict[int, int] = {}  # lower edge in ms -> looks
        # The two clocks that split a stall into what this process spent
        # and what it was not running for. Injected rather than read at
        # the call site: the caller's `at` times its own loop, while
        # these exist only to attribute what that timing found.
        self._cpu_now, self._gc_now = cpu_clock, gc_clock
        self._cpu = self._gc = 0.0
        self._worst_cpu = self._worst_gc = 0.0

    def looked(self, records: list[Record], at: float) -> None:
        """Record one opportunity to drain, and what it found.

        The interval is between looks, not between drains that found
        something: a ring that was empty was never at risk, so counting
        idle stretches would turn the worst case into a measure of how
        quiet the run was.

        The rate is per ring, because the depth is per ring. A record's
        `cpu` names the ring that wrote it, and a total across four of
        them would claim a horizon four times shorter than the real one.

        How fast a ring filled is read off the records' own timestamps,
        not off the interval that collected them. The two agree only
        while the reader keeps up: a bounded drain working through a
        backlog hands over records made across a second inside a turn of
        a few milliseconds, and dividing by the turn would call that a
        fill rate the firmware never reached — shrinking the horizon on
        evidence that the reader, not the machine, was busy.
        """
        spent, paused = self._cpu_now(), self._gc_now()
        if self._at is not None:
            interval = at - self._at
            edge = _gap_bucket(interval)
            self._gaps[edge] = self._gaps.get(edge, 0) + 1
            if interval > self._worst:
                # The composition is taken where the stall is, so the two
                # can never describe different intervals.
                self._worst = interval
                self._worst_cpu = spent - self._cpu
                self._worst_gc = paused - self._gc
        self._at, self._cpu, self._gc = at, spent, paused
        self._fill(records)

    def _fill(self, records: list[Record]) -> None:
        """Fastest per-ring production, counted over the declared window.

        A gap record stands for what never arrived and carries a stamp
        the reader chose, so counting it would inflate the rate exactly
        where the ring was already losing.
        """
        if not self._freq:
            return
        for record in records:
            if record.code != GAP_CODE:
                self._count(record.cpu, record.ts)

    def _count(self, ring: int, ts: int) -> None:
        counts = self._slices.setdefault(ring, deque([0], maxlen=_RATE_SLICES))
        start = self._opened.setdefault(ring, ts)
        if ts - start >= self._window:
            # Nothing was written for a whole window; what is held says
            # nothing about the rate around this record.
            counts.clear()
            counts.append(0)
            start = ts
        while ts - start >= self._slice:
            counts.append(0)
            start += self._slice
        self._opened[ring] = start
        counts[-1] += 1
        # A partial window would divide a fraction of the records by the
        # whole span and report a rate the ring never ran at.
        if len(counts) == _RATE_SLICES:
            self._peak = max(self._peak, sum(counts) / HORIZON_SECONDS)

    @property
    def horizon_seconds(self) -> float:
        """How long the ring covers at the fastest fill yet seen.

        Zero until something has been seen: an unmeasured budget is not
        an unlimited one.
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
            # What the worst stall was made of. The rest of it — the gap
            # less these two — is time the process was not running: the
            # loop's own wait, and beyond that, contention for the host.
            "worst_cpu_ms": round(self._worst_cpu * 1000, 1),
            "worst_gc_ms": round(self._worst_gc * 1000, 1),
            "horizon_ms": round(self.horizon_seconds * 1000, 1),
            "overrun": self.overrun,
            "gaps": {str(edge): self._gaps[edge] for edge in sorted(self._gaps)},
        }


class TraceReader:
    """One run's rings, read from the shared RAM file.

    `ram_base` is where the machine's RAM aperture starts, so a physical
    address is read at `pa - ram_base` — the same constant the S layer's
    provider uses, and the only address arithmetic here.

    `region_size` comes from the board too. It is how much a board spent
    on the T layer, and every ring's depth follows from it, so a copy
    kept here would be this reader's opinion of another machine's map.
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
        # The last timestamp handed out per ring: where the next hole
        # opens, carried by a drain that recovered nothing until a
        # record gives the hole somewhere to end.
        self._last_ts = [0] * self.geometry.rings
        self._pending = [0] * self.geometry.rings
        # Pre-placement drops, waiting for a timestamp to sit at.
        self._early_pending = self.geometry.early

    def _read_geometry(self) -> Geometry:
        magic, version, record_size, stride, rings, capacity, freq, early = _HEADER.unpack_from(
            self._ram, self._offset
        )
        if magic != MAGIC:
            # The magic is written last, so its absence says only that
            # nobody has finished placing a region here. Stale bytes
            # from a previous boot read the same way and mean the same.
            raise NotYetFormatted(f"no trace region at {self._offset:#x} (magic {magic:#x})")
        if version != VERSION:
            raise NotFormatted(f"trace region version {version}, expected {VERSION}")
        if record_size != REC_SIZE:
            raise NotFormatted(f"trace record is {record_size} bytes, expected {REC_SIZE}")
        # The depth is the region divided by the ring count, so it
        # arrives here and nowhere else — there is no constant left to
        # check it against, which makes vetting it this reader's job.
        # Every number below is one it is about to index with.
        if not 1 <= rings <= MAX_RINGS:
            raise NotFormatted(f"trace region declares {rings} rings, expected 1..{MAX_RINGS}")
        if capacity < 1 or capacity & (capacity - 1):
            raise NotFormatted(f"trace ring capacity {capacity} is not a power of two")
        if stride != stride_for(capacity):
            raise NotFormatted(f"trace stride {stride} disagrees with capacity {capacity}")
        if _HEADER_SIZE + rings * stride > self._region_size:
            raise NotFormatted(f"{rings} rings of {capacity} do not fit {self._region_size} bytes")
        return Geometry(rings, capacity, stride, freq, early)

    def _record(self, at: int) -> Record:
        # The reserved byte is unpacked and dropped: it belongs to the
        # layout, not to the event.
        return unpack_from(self._ram, at)

    def _head(self, ring: int) -> int:
        base = self._offset + _HEADER_SIZE + ring * self.geometry.stride
        return int.from_bytes(self._ram[base + _HEAD_OFF : base + _HEAD_OFF + 8], "little")

    def pending(self) -> int:
        """Records waiting across every ring.

        Two eight-byte reads per ring and no decode, so a caller can ask
        far more often than it can afford to drain — which is what lets
        an idle tick skip the work rather than budget for it.
        """
        return sum(
            max(0, self._head(ring) - self._cursor[ring])
            for ring in range(self.geometry.rings)
        )

    def drain(self, limit: int | None = None) -> list[Record]:
        """Everything written since the last call, oldest first, plus a
        record for everything that was not.

        `limit` caps how many records this call may decode, so one drain
        costs a bounded amount of the caller's time no matter how far
        behind it is. Without it the cost is the backlog, the backlog is
        the last call's duration, and the two feed each other.

        Ordered across rings by timestamp. CNTPCT is common to every PE,
        so that ordering is the machine's real one — the thing a sampled
        layer cannot supply at any rate.

        A hole comes back as a record rather than a count beside the
        list. This function knows both of its ends; a caller handed an
        integer can only say something was lost, never that it was lost
        *there*, and two marks drawn as neighbours across eight thousand
        missing records make the causal chain a reader takes from them
        fiction.

        Every head is read before any ring is copied. Reading one head
        and copying before looking at the next skews the batch boundary
        by the length of that copy: a record written to an already-read
        ring in that window waits for the next drain and arrives stamped
        earlier than one this drain is about to hand over. Snapshotting
        the heads shrinks the window to a few eight-byte reads.
        """
        heads = [self._head(ring) for ring in range(self.geometry.rings)]
        if limit is not None:
            heads = self._bounded(heads, limit)
        found: list[Record] = []
        for ring, head in enumerate(heads):
            found += self._drain_one(ring, head)
        found.sort(key=lambda record: record.ts)
        return self._with_early(found)

    def _slot(self, ring: int, index: int) -> int:
        return (
            self._offset
            + _HEADER_SIZE
            + ring * self.geometry.stride
            + _RECORDS_OFF
            + (index % self.geometry.capacity) * REC_SIZE
        )

    def _bounded(self, heads: list[int], limit: int) -> list[int]:
        """Heads moved back so the batch is both small and whole.

        Small alone is not enough. A ring stopped short leaves records
        older than ones another ring just handed over, and the next
        batch would then carry records the last one had already passed —
        the buffer holding them in time order pays for that by lifting
        everything newer, one record at a time, which is the stall this
        cap exists to prevent moved somewhere else.

        So the batch ends at a moment rather than at a count: the oldest
        record being left behind sets the cutoff, and every ring stops at
        or below it. The ring that sets the cutoff hands over its whole
        share, so a bounded drain always advances.
        """
        starts = [
            max(self._cursor[ring], self._oldest_intact(head))
            for ring, head in enumerate(heads)
        ]
        stops = self._share(starts, heads, limit)
        behind = [ring for ring, stop in enumerate(stops) if stop < heads[ring]]
        if not behind:
            return stops
        cutoff = min(self._record(self._slot(ring, stops[ring])).ts for ring in behind)
        return [
            self._up_to(ring, starts[ring], stops[ring], cutoff)
            for ring in range(self.geometry.rings)
        ]

    def _share(self, starts: list[int], heads: list[int], limit: int) -> list[int]:
        """Split the allowance across rings, then hand what a quiet ring
        did not want to the ones that did."""
        rings = self.geometry.rings
        want = [head - start for start, head in zip(starts, heads)]
        take = [min(pending, max(1, limit // rings)) for pending in want]
        spare = limit - sum(take)
        for ring in range(rings):
            if spare <= 0:
                break
            more = min(spare, want[ring] - take[ring])
            take[ring] += more
            spare -= more
        return [start + taken for start, taken in zip(starts, take)]

    def _up_to(self, ring: int, start: int, stop: int, cutoff: int) -> int:
        """The first index in this ring at or after `stop` whose record
        is newer than the cutoff.

        A ring is written by one PE in order, so its slots are sorted by
        timestamp and the boundary is found without decoding the span.
        """
        low, high = start, stop
        while low < high:
            middle = (low + high) // 2
            if self._record(self._slot(ring, middle)).ts <= cutoff:
                low = middle + 1
            else:
                high = middle
        return low

    def _with_early(self, records: list[Record]) -> list[Record]:
        """Fold the pre-placement drops in, once, at the front.

        A different loss from a lapped ring — these predate the region,
        so no drain however prompt could have caught them — but the same
        hole in the run, and early boot is where a reader is most likely
        to be looking. `b` is zero: nothing precedes them, so the hole
        has no near end.
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

        One short of the capacity. Head at H means the writer has
        published H records and is inside the slot for index H, which is
        the slot index H - capacity occupies, so that record is already
        being destroyed and a reader keeping the last `capacity` would
        hand out one record assembled from two events. It costs nothing
        while the reader keeps up: the cursor is newer than this bound.
        """
        capacity = self.geometry.capacity
        return head - capacity + 1 if head >= capacity else 0

    def _drain_one(self, ring: int, head: int) -> list[Record]:
        capacity = self.geometry.capacity
        base = self._offset + _HEADER_SIZE + ring * self.geometry.stride + _RECORDS_OFF
        cursor = self._cursor[ring]
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
        # Re-read: anything the writer lapped during the copy has fallen
        # out of the window, so it is discarded rather than trusted.
        # Reading a record mid-write is only possible through this race.
        safe = self._oldest_intact(self._head(ring))
        if safe > keep:
            del records[: min(safe - keep, len(records))]
        self._cursor[ring] = head

        missed = (head - cursor) - len(records) + self._pending[ring]
        if not records:
            # Nothing survived, so there is no timestamp to close a hole
            # on. The count is deferred, never dropped: the drain that
            # can say where the hole ends places it.
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

    def snapshot_heads(self) -> tuple[int, ...]:
        """Where every ring's writer stood at one instant.

        Taken once the producer is gone, so the values do not move again
        and "drained" becomes a question with an answer.
        """
        return tuple(self._head(ring) for ring in range(self.geometry.rings))

    def behind(self, heads: tuple[int, ...]) -> bool:
        """Whether any cursor still trails the heads it was given."""
        return any(
            cursor < head for cursor, head in zip(self._cursor, heads, strict=True)
        )

    def held_losses(self) -> int:
        """Counted losses no record has been able to carry out yet."""
        return sum(self._pending) + self._early_pending

    def flush_held(self) -> list[Record]:
        """Turn the held losses into gap records, once, at the end.

        A held count needs a surviving record to sit beside: `_drain_one`
        defers it until one arrives, and `_with_early` folds the
        pre-placement drops into the first batch that has one. With the
        producer gone and every cursor at its head, no record is coming,
        so the count would be lost with the reader.

        Emitted here instead, through the same record shape the drain
        uses, so history, the recording and the ledger see one kind of
        fact and not two. The hole opens where this ring last handed out
        a record and does not close: nothing follows it.
        """
        held: list[Record] = []
        for ring, missed in enumerate(self._pending):
            if not missed:
                continue
            at = self._last_ts[ring]
            held.append(
                Record(ts=at, code=GAP_CODE, cpu=ring, a=min(missed, _MAX_COUNT), b=at, c=0)
            )
        self._pending = [0] * self.geometry.rings
        if self._early_pending:
            early, self._early_pending = self._early_pending, 0
            # It precedes every record this reader saw, so it anchors on
            # the earliest timestamp any ring has handed out.
            at = min(self._last_ts)
            held.insert(
                0, Record(ts=at, code=GAP_CODE, cpu=0, a=min(early, _MAX_COUNT), b=0, c=0)
            )
        return held

    def close(self) -> None:
        self._ram.close()


@dataclass(frozen=True)
class RunTotals:
    """What one run's records add up to, and whether that is all of them.

    `lost` is every gap record's count summed — pre-placement drops, a
    lapped ring, and the terminal flush alike. The kinds are not told
    apart because completeness does not need them told apart: an early
    hole and a first lap can be written identically in the raw stream
    (both carry b=0 when nothing precedes them), so a reader that claimed
    to distinguish them would be claiming more than the records say.

    `absent` is a different axis from `complete`: an image built without
    a trace ring was never measured, which is not the same as measured
    and found wanting.

    Three of the fields are raw and the rest follow from them and the
    records. Kept together because a run's completeness is one
    description: split across the wire and a file, the two would answer
    differently about the same run.
    """

    events: dict[int, int]
    lost: int
    producer_dead: bool
    tail_drained: bool
    absent: bool = False

    #: The facts the records cannot recover on their own, so the only
    #: ones a recording has to carry for its totals to be re-derived.
    RAW = ("producer_dead", "tail_drained", "absent")

    @property
    def complete(self) -> bool:
        return not self.absent and self.lost == 0 and self.tail_drained

    def raw(self) -> dict:
        """What a file must store; everything else follows from the records."""
        return {name: getattr(self, name) for name in self.RAW}

    def as_dict(self) -> dict:
        return {
            "events": {str(code): count for code, count in sorted(self.events.items())},
            "lost": self.lost,
            **self.raw(),
            "complete": self.complete,
        }


class RunLedger:
    """One run's totals, accumulated from the records as they are consumed.

    Beside the per-batch summary rather than instead of it: the batch
    counts are what the UI draws and are scoped to a frame, while this
    answers what the whole run did. One stream of records, two readings.
    """

    def __init__(self) -> None:
        self._events: Counter[int] = Counter()
        self._lost = 0

    def consume(self, records: list[Record]) -> None:
        for record in records:
            if record.code == GAP_CODE:
                self._lost += record.a
                continue
            self._events[record.code] += 1

    def seal(
        self, *, producer_dead: bool, tail_drained: bool, absent: bool = False
    ) -> RunTotals:
        return RunTotals(
            events=dict(self._events),
            lost=self._lost,
            producer_dead=producer_dead,
            tail_drained=tail_drained,
            absent=absent,
        )


def dropped_in(records: list[Record]) -> int:
    """Events the gap records in this batch account for.

    The one place the number is computed, so a badge showing a count and
    a window drawing the holes are two views of one drain rather than
    two accounts of it.
    """
    return sum(record.a for record in records if record.code == GAP_CODE)


def summarise(records: list[Record]) -> dict:
    """What crosses the wire: counts per path, and the last of each.

    The records themselves stay here. A few thousand events a second is
    nothing to a bridge and a great deal to a browser, and a cap with a
    silent drop would make "everything that happened" a lie. The board
    needs which paths fired and how often; a reader who wants the events
    asks for them.

    A record the catalogue marks as a reply travels beside them, off the
    edge index: it sits on no path, and the control that asked for it
    needs it back. A batch without one leaves the field absent and the
    last verdict standing.
    """
    edges: dict[str, int] = {}
    last: dict[str, dict] = {}
    answered: dict | None = None
    for record in records:
        entry = events.BY_CODE.get(record.code)
        if entry is None:
            continue
        if entry.reply:
            answered = decode(record)
        if not entry.edge:
            continue
        edges[entry.edge] = edges.get(entry.edge, 0) + 1
        last[entry.edge] = decode(record)
    summary = {"edges": edges, "last": last, "dropped": dropped_in(records)}
    return summary if answered is None else summary | {"command": answered}


def histogram(records: list[Record], first: int, last: int, buckets: int) -> dict[str, list[int]]:
    """How many of each event fell in each column of a window.

    Always the whole window: a reader who dragged the window out wants
    to know what happened and how much of it, and a 1200-pixel strip
    could not draw fifty thousand separate marks anyway.

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


def window(
    packed: bytes,
    first: int,
    last: int,
    buckets: int,
    wanted: set[str],
) -> tuple[int, dict[str, list[int]], dict[str, list[int]] | None]:
    """Count, chart, and conditionally retain one packed window.

    The density needs every record, while marks are useful only when
    they fit the caller's columns. Keep primitive mark fields until the
    count crosses that limit, then discard them and finish the same
    scan for the density.
    """
    span = max(1, last - first + 1)
    hist: dict[str, list[int]] = {}
    cols: dict[str, list[int]] | None = {
        "ts": [],
        "code": [],
        "cpu": [],
        "a": [],
        "b": [],
        "c": [],
    }
    count = 0
    for ts, code, cpu, _flags, a, b, c in _RECORD.iter_unpack(packed):
        entry = events.BY_CODE.get(code)
        if wanted and (entry is None or entry.id not in wanted):
            continue
        count += 1
        if entry is not None:
            column = min(buckets - 1, (ts - first) * buckets // span)
            lane = hist.get(entry.id)
            if lane is None:
                lane = hist[entry.id] = [0] * buckets
            lane[max(0, column)] += 1
        if cols is None:
            continue
        if count > buckets:
            cols = None
            continue
        cols["ts"].append(ts - first)
        cols["code"].append(code)
        cols["cpu"].append(cpu)
        cols["a"].append(a)
        cols["b"].append(b)
        cols["c"].append(c)
    return count, hist, cols


def decode(record: Record) -> dict:
    """A record as the event it is, with its arguments named.

    The firmware packs the two INTIDs of a binding into one word — high
    half physical — because that pairing is the whole content of the
    event. Unpacking here keeps the packing a detail of the wire between
    EL2 and this file.
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
    elif entry.id == "smmu.attach":
        out |= {"stream": record.a, "root": f"{record.b:#x}", "vmid": record.c}
    elif entry.id == "dma.start":
        out |= {"vm": record.a, "address": f"{record.b:#x}", "bytes": record.c}
    elif entry.id == "command":
        # EL2 packs the opcode and the verdict into one word; the halves
        # and their names both come from the ABI header, so a refusal
        # says what kind it was rather than which number it was.
        out |= {
            "op": commands.op_name(record.a & commands.ANSWER_MASK),
            "result": commands.result_name(record.a >> commands.ANSWER_SHIFT),
            "a": record.b,
            "b": record.c,
        }
    elif entry.id == "trace.gap":
        # The width, not the far end: `b` is a raw counter value, which
        # is the one thing no reader can use. Zero when the hole opened
        # before anything was recorded, so it has no measurable start.
        out |= {"count": record.a, "ticks": record.ts - record.b if record.b else 0}
    return out


def report(shm_path: Path, ram_base: int, trace_pa: int, region_size: int, limit: int = 40) -> int:
    """The terminal twin of the T layer, read off the rings themselves.

    The fallback path: no bridge, so no history, and what arrives here
    is only what the rings still hold. Needs no browser and no image
    either, which is what makes it the answer when nothing else runs.
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
        # One rule rather than a case per event that has one.
        if "ticks" in fields:
            fields["ticks"] = f"{_micros(fields['ticks'], freq_hz)}us"
        detail = " ".join(
            f"{key}={value}" for key, value in fields.items() if key not in ("event", "cpu", "ts")
        )
        # Relative to the first record shown: absolute counter values say
        # nothing a reader can use, and the frequency is right here.
        print(
            f"  {_micros(record.ts - base, freq_hz):>10}us "
            f"cpu{record.cpu} {fields['event']:<13} {detail}"
        )


def _micros(ticks: int, freq_hz: int) -> int:
    return ticks * 1_000_000 // max(1, freq_hz)
