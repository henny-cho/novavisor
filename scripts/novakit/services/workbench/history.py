"""The bridge's memory of a run: an overwriting ring of drained records.

The firmware's rings hold about 2.7 seconds at the measured event rate,
and a great deal less than that through a burst. That is the right size
for what they are — a handover buffer, whose capacity is a *latency*
budget and not a memory — but it means that by the time a reader has
noticed something, its cause is already overwritten. Memory belongs in
the layer that has memory.

So the same discipline moves up one level: fixed budget, overwrite
rather than block, and a reader that works out what it can no longer
see. Copying the discipline is not the same as copying the numbers, and
in one place they must differ. Two things here look like "the writer
lapped the reader" and mean opposite things:

    the firmware ring wrapped before a drain   the bridge was late
    this history wrapped onto its own oldest   the horizon, working

Reporting the second as `dropped` would make the one actionable number
in the T layer permanently non-zero on any session older than a few
minutes, which is a diagnostic dying quietly. This publishes a `span`
instead: what it still holds, and whether it has been round once.

Records are kept as the 32 raw bytes the firmware wrote. Held as Record
objects the same count costs several times the memory, and the decode
is only ever wanted for the window somebody actually asked about.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import trace

# Records the history holds. 2^19 * 32 B = 16 MiB, which at the measured
# ~1500 events/s is about six minutes, and about four through the higher
# rate the full hook set produces. Not a duration, because the same
# budget is twenty quiet minutes or three busy ones — which is why the
# span goes on the wire rather than being left for a reader to discover
# by hitting it.
DEFAULT_CAPACITY = 1 << 19


@dataclass(frozen=True)
class Span:
    """What the history still holds, as the wire states it."""

    first: int  # oldest timestamp retained, 0 when empty
    last: int  # newest timestamp retained, 0 when empty
    count: int  # records retained
    full: bool  # has wrapped: nothing before `first` exists any more

    def as_dict(self) -> dict:
        return {"from": self.first, "to": self.last, "n": self.count, "full": self.full}


class History:
    """Drained records, oldest evicted first, in one fixed allocation.

    Timestamps are non-decreasing across appends because drain() merges
    the per-CPU rings by CNTPCT before handing them over, and CNTPCT is
    common to every PE. That is what lets a window be found by bisection
    rather than by scanning, and it is a property of the drain — this
    class asserts nothing about records appended out of order beyond
    keeping them in arrival order.
    """

    def __init__(self, capacity: int = DEFAULT_CAPACITY):
        if capacity < 1:
            raise ValueError("history capacity must be at least one record")
        self.capacity = capacity
        self._bytes = bytearray(capacity * trace.REC_SIZE)
        # Total ever appended. The live window is the last `capacity` of
        # them, so "how much was forgotten" is arithmetic and not a
        # counter that could drift from the buffer it describes.
        self._head = 0

    def __len__(self) -> int:
        return min(self._head, self.capacity)

    @property
    def full(self) -> bool:
        return self._head > self.capacity

    def append(self, records: list[trace.Record]) -> None:
        for record in records:
            trace.pack_into(self._bytes, (self._head % self.capacity) * trace.REC_SIZE, record)
            self._head += 1

    def span(self) -> Span:
        held = len(self)
        if held == 0:
            return Span(0, 0, 0, False)
        return Span(self._at(0), self._at(held - 1), held, self.full)

    def _slot(self, index: int) -> int:
        """Where the index'th oldest retained record sits in the ring."""
        return (self._head - len(self) + index) % self.capacity

    def _at(self, index: int) -> int:
        """Timestamp of the index'th oldest retained record.

        Read back out of the buffer rather than mirrored into an array
        beside it. A mirror is one more thing append() has to keep true,
        and the first edit that forgets gives a bisection searching the
        wrong order over records that are all still there — silently.
        Eight bytes at a known offset is what the layout is for.
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

    def window(self, first: int, last: int) -> list[trace.Record]:
        """Every retained record with `first` <= ts <= `last`.

        Both ends inclusive: a caller asking about the instant of a mark
        means to include it.
        """
        start = self._bisect(first)
        stop = self._bisect(last + 1)
        return [self._record(index) for index in range(start, stop)]
