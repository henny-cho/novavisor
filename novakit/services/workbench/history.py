"""The bridge's memory of a run: an overwriting ring of drained records.

The firmware's rings are sized for one second at the peak fill a board
declares. That is the right size for a handover buffer, whose capacity
is a latency budget rather than a memory, but it means the cause of
anything a reader notices late is already overwritten down there.

The same discipline moves up one level — fixed budget, overwrite rather
than block, reader works out what it can no longer see — with one
deliberate difference in what a wrap means. Two events look alike and
are opposites:

    the firmware ring wrapped before a drain   the bridge was late
    this history wrapped onto its own oldest   the horizon, working

Reporting the second as `dropped` would leave the T layer's one
actionable number permanently non-zero on any session more than a few
minutes old. This publishes a `span` instead: what it still holds, and
whether it has been round once.

Records are kept as the 32 raw bytes the firmware wrote. As Record
objects the same count costs several times the memory, and the decode is
only ever wanted for the window somebody asked about.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import trace

# Records the history holds: 2^19 * 32 B = 16 MiB of the bridge's memory.
# Stated in records because that is the cost, and not in seconds because
# the same budget is twenty quiet minutes or one Linux boot — which is
# why the span goes on the wire rather than being left for a reader to
# discover by hitting it.
DEFAULT_CAPACITY = 1 << 19


@dataclass(frozen=True)
class Span:
    """What the history still holds, as the wire states it."""

    first: int  # oldest timestamp retained, 0 when empty
    last: int  # newest timestamp retained, 0 when empty
    count: int  # records retained
    full: bool  # has wrapped: nothing before `first` exists any more
    # Ticks per second, so the two ends above form a duration rather than
    # two opaque counters. Both consumers ask for "the last N seconds",
    # and without this both computed with a frequency of zero.
    freq_hz: int = 0

    def as_dict(self) -> dict:
        return {
            "from": self.first,
            "to": self.last,
            "n": self.count,
            "full": self.full,
            "freq_hz": self.freq_hz,
        }


class History:
    """Drained records, in time order, in one fixed allocation.

    Time order is what lets a window be found by bisection rather than by
    scanning, and append() enforces it rather than assuming it: the drain
    sorts each batch, but the stream is those batches concatenated and a
    boundary between two of them can step backwards.
    """

    def __init__(self, capacity: int = DEFAULT_CAPACITY):
        if capacity < 1:
            raise ValueError("history capacity must be at least one record")
        self.capacity = capacity
        self._bytes = bytearray(capacity * trace.REC_SIZE)
        # The clock the timestamps are in, learned when a reader attaches
        # and reads the region header. Published with the span.
        self.freq_hz = 0
        # Total ever appended. The live window is the last `capacity` of
        # them, so "how much was forgotten" is arithmetic rather than a
        # counter that could drift from the buffer it describes.
        self._head = 0

    def reset(self) -> None:
        """Start a new epoch, keeping the allocation.

        A new run's timestamps restart low, and appending them to the
        last run's puts both in one order: every record of the new run is
        older than everything held, so append() lifts the whole buffer
        and evicts each one by its own reinstatement. Everything the
        history reports is derived from `_head`, so winding it back is
        the whole of being empty.
        """
        self._head = 0
        self.freq_hz = 0

    def __len__(self) -> int:
        return min(self._head, self.capacity)

    @property
    def full(self) -> bool:
        return self._head > self.capacity

    def append(self, records: list[trace.Record]) -> None:
        """Add a drained batch, keeping the buffer in time order.

        In practice a comparison and nothing else: the drain reads every
        ring head before copying anything, so an out-of-order record is
        rare and lands one or two slots back when it happens.
        """
        for record in records:
            self._insert(record)

    def _push(self, record: trace.Record) -> None:
        trace.pack_into(self._bytes, (self._head % self.capacity) * trace.REC_SIZE, record)
        self._head += 1

    def _insert(self, record: trace.Record) -> None:
        held = len(self)
        if not held or record.ts >= self._at(held - 1):
            self._push(record)
            return
        # Lift the few newer records, lay this one under them, put them
        # back. A record older than everything held lifts the whole
        # buffer and is evicted by its own reinstatement, which is the
        # right answer: it belongs to a horizon already let go of.
        later = []
        while len(self) and self._at(len(self) - 1) > record.ts:
            later.append(self._record(len(self) - 1))
            self._head -= 1
        self._push(record)
        for held_back in reversed(later):
            self._push(held_back)

    def span(self) -> Span:
        held = len(self)
        if held == 0:
            return Span(0, 0, 0, False, self.freq_hz)
        return Span(self._at(0), self._at(held - 1), held, self.full, self.freq_hz)

    def _slot(self, index: int) -> int:
        """Where the index'th oldest retained record sits in the ring."""
        return (self._head - len(self) + index) % self.capacity

    def _at(self, index: int) -> int:
        """Timestamp of the index'th oldest retained record.

        Read back out of the buffer rather than mirrored into an array
        beside it: a mirror is a second thing append() has to keep true,
        and the first edit that forgets leaves the bisection searching
        the wrong order over records that are all still there.
        """
        return trace.timestamp_at(self._bytes, self._slot(index) * trace.REC_SIZE)

    def _record(self, index: int) -> trace.Record:
        return trace.unpack_from(self._bytes, self._slot(index) * trace.REC_SIZE)

    def _bisect(self, ts: int) -> int:
        """First retained index whose timestamp is >= ts.

        Written out rather than handed to the bisect module because the
        sequence is a ring: index 0 is wherever the oldest survivor
        landed, and there is no contiguous list to slice.
        """
        low, high = 0, len(self)
        while low < high:
            middle = (low + high) // 2
            if self._at(middle) < ts:
                low = middle + 1
            else:
                high = middle
        return low

    def slice(self, first: int, last: int) -> bytes:
        """The packed records of this window, copied out of the ring.

        Copied rather than viewed, and in whole runs rather than record
        by record: the drain writes into this buffer while it works, and
        a reader unpacking straight from it would decode one record out
        of two. The copy is two memcpys — the retained run is contiguous
        unless it crosses the ring's seam, and then it is exactly two
        pieces.

        This half has to happen where the drain cannot interleave with
        it. Turning the bytes into records is the other half, costs a
        hundred times as much, and can happen anywhere.
        """
        start, stop = self._bisect(first), self._bisect(last + 1)
        if start >= stop:
            return b""
        head, count = self._slot(start), stop - start
        ahead = min(count, self.capacity - head)
        # Through a memoryview so each run is copied once: slicing the
        # bytearray itself would build a bytearray and then a bytes of it.
        held = memoryview(self._bytes)
        out = bytes(held[head * trace.REC_SIZE : (head + ahead) * trace.REC_SIZE])
        if ahead < count:
            out += bytes(held[: (count - ahead) * trace.REC_SIZE])
        return out

    def window(self, first: int, last: int) -> list[trace.Record]:
        """Every retained record with `first` <= ts <= `last`.

        Both ends inclusive: a caller asking about the instant of a mark
        means to include it.
        """
        return trace.unpack_all(self.slice(first, last))
