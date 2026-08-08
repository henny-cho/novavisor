"""The bridge's memory of a run.

The firmware's rings hold about a second, so by the time a reader
notices something its cause is already overwritten there. This is where
it is still findable: the same discipline one layer up, with one
deliberate difference in what a wrap means.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from novakit.services.workbench import history, trace  # noqa: E402


def records(stamps, code: int = 1) -> list[trace.Record]:
    return [trace.Record(ts=ts, code=code, cpu=ts & 1, a=ts, b=ts * 2, c=ts * 3) for ts in stamps]


def stamps(held) -> list[int]:
    """What the buffer holds, oldest first, read back out of it.

    Through the window rather than the internals: the order this checks
    is the order a reader gets, which is the only one that matters.
    """
    span = held.span()
    return [record.ts for record in held.window(span.first, span.last)]


class AppendTest(unittest.TestCase):
    def test_records_survive_the_round_trip_as_written(self):
        """Stored as the firmware's own 32 bytes: as Record objects the
        same count costs several times the memory, and the decode is
        only ever wanted for the window somebody asked about."""
        ring = history.History(capacity=8)
        ring.append(records([10, 20, 30]))

        got = ring.window(0, 100)
        self.assertEqual([r.ts for r in got], [10, 20, 30])
        self.assertEqual(got[1].cpu, 0)
        self.assertEqual(got[1].a, 20)
        self.assertEqual(got[1].b, 40)
        self.assertEqual(got[1].c, 60)

    def test_an_empty_history_spans_nothing_and_says_so(self):
        span = history.History(capacity=8).span()
        self.assertEqual(
            span.as_dict(), {"from": 0, "to": 0, "n": 0, "full": False, "freq_hz": 0}
        )

    def test_the_span_carries_the_clock_its_timestamps_are_in(self):
        """A range of counter values is not a duration without it, and
        both consumers ask for "the last N seconds" of the history."""
        ring = history.History(capacity=8)
        ring.freq_hz = 62_500_000
        ring.append(records([10, 20]))
        self.assertEqual(ring.span().as_dict()["freq_hz"], 62_500_000)


class WrapTest(unittest.TestCase):
    """The difference that matters between this and the ring below it."""

    def test_the_oldest_go_and_the_budget_does_not_move(self):
        ring = history.History(capacity=4)
        ring.append(records([1, 2, 3, 4, 5, 6]))

        self.assertEqual(len(ring), 4)
        self.assertEqual([r.ts for r in ring.window(0, 100)], [3, 4, 5, 6])

    def test_a_wrap_is_published_as_a_horizon_not_as_a_loss(self):
        """Reporting this as `dropped` would leave the one actionable
        number in the T layer permanently non-zero on any session older
        than a few minutes — an alarm lit for a working condition."""
        ring = history.History(capacity=4)
        ring.append(records([1, 2, 3]))
        self.assertFalse(ring.span().full)

        ring.append(records([4, 5]))
        span = ring.span()
        self.assertTrue(span.full)
        # And the span states exactly what survived it.
        self.assertEqual((span.first, span.last, span.count), (2, 5, 4))

    def test_a_history_of_one_still_answers(self):
        ring = history.History(capacity=1)
        ring.append(records([7, 8]))
        self.assertEqual([r.ts for r in ring.window(0, 100)], [8])
        self.assertTrue(ring.span().full)

    def test_a_batch_that_starts_before_the_last_one_ended_is_put_in_order(self):
        """A batch is not the stream. The drain sorts what it hands
        over, but the boundary between two of them can go backwards by
        however far the per-ring head reads were skewed, and everything
        that reads this searches it by bisection.
        """
        held = history.History(64)
        held.append(records([10, 20, 30, 40]))
        held.append(records([25, 35, 50]))  # the boundary steps back
        self.assertEqual(stamps(held), [10, 20, 25, 30, 35, 40, 50])

    def test_a_record_older_than_the_horizon_falls_out_rather_than_lands_wrong(self):
        """It belongs to a stretch this history has already let go of,
        and putting it at either end would be a claim about order that
        is not true."""
        held = history.History(4)
        held.append(records([10, 20, 30, 40, 50]))  # 10 is already gone
        held.append(records([15]))
        self.assertEqual(stamps(held), [20, 30, 40, 50])

    def test_the_order_holds_across_the_wrap(self):
        held = history.History(4)
        held.append(records([10, 20, 30, 40, 60, 70]))
        held.append(records([65]))
        self.assertEqual(stamps(held), [40, 60, 65, 70])

    def test_a_capacity_of_zero_is_refused(self):
        with self.assertRaises(ValueError):
            history.History(capacity=0)


class WindowTest(unittest.TestCase):
    """Finding a window is a bisection, so the buffer has to be in time
    order, which append() enforces rather than inherits.

    CNTPCT being common to every PE makes each drained batch sorted; it
    does not make the concatenation of two batches sorted."""

    def setUp(self):
        self.ring = history.History(capacity=64)
        self.ring.append(records(range(0, 200, 10)))  # 0, 10, ... 190

    def test_both_ends_are_inclusive(self):
        """A reader asking about the instant of a mark means to have it."""
        self.assertEqual([r.ts for r in self.ring.window(50, 70)], [50, 60, 70])

    def test_a_window_between_records_is_empty_rather_than_nearest(self):
        self.assertEqual(self.ring.window(51, 59), [])

    def test_a_window_past_either_end_is_clamped_to_what_is_held(self):
        self.assertEqual(len(self.ring.window(-5, 10_000)), 20)
        self.assertEqual(self.ring.window(1_000, 2_000), [])

    def test_a_window_survives_the_wrap_it_straddles(self):
        ring = history.History(capacity=4)
        ring.append(records([1, 2, 3, 4, 5, 6, 7]))
        # 1..3 are gone; asking across the horizon returns what is left
        # rather than failing or inventing.
        self.assertEqual([r.ts for r in ring.window(1, 5)], [4, 5])

    def test_duplicate_timestamps_are_all_returned(self):
        """Two cores can stamp the same tick, and a window that dropped
        one of them would break the ordering it exists to show."""
        ring = history.History(capacity=8)
        ring.append(records([5, 5, 5]))
        self.assertEqual(len(ring.window(5, 5)), 3)


if __name__ == "__main__":
    unittest.main()
