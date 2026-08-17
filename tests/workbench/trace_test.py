"""The T-layer reader against a region this test writes itself.

The firmware side is proven beside the ring it writes, in
src/nova/test/trace_ring_test.cpp, against a real concurrent writer. What is left to hold here is the reader's half
of the same contract: that it finds the region without any debug info,
refuses a layout it does not understand instead of decoding it, and
accounts for every record between its cursor and the writer's head.
"""

from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from novakit.image import abi
from novakit.services.workbench import events, trace

L = abi.read_defines(
    abi.TRACE_RING,
    ["NOVA_TRACE_HEADER_SIZE", "NOVA_TRACE_RECORDS_OFF", "NOVA_TRACE_REC_SIZE"],
)
# How much a board spends on the T layer is that board's decision, so a
# fixture states its own rather than borrowing one. Room for a few rings
# of CAPACITY and nothing more — the arithmetic under test is
# `pa - ram_base`, and a real board's 16 MiB would make every case a
# 16-megabyte file.
REGION_SIZE = 0x10000
RAM_BASE = 0x4000_0000
# Close to the base on purpose: what matters is the `pa - ram_base`
# arithmetic, and the real board offset would make every fixture a
# half-gigabyte file.
TRACE_PA = 0x4000_1000
CAPACITY = 16  # small enough that lapping is easy to arrange


class Region:
    """A trace region on disk, written the way the firmware writes one.

    The header is laid out by the reader's own packer, so a fixture
    cannot drift from the layout it is testing against. The geometry
    fields are separately overridable because that is the only way to
    write a header the firmware could not have written — which is what
    the reader's vetting is for. Left alone they stay consistent with
    each other, so a test about draining never trips a check about
    layout.
    """

    def __init__(self, rings: int = 2, *, magic: int | None = None, version: int | None = None,
                 capacity: int = CAPACITY, early: int = 0,
                 header_rings: int | None = None, stride: int | None = None):
        self.rings = rings
        self.capacity = capacity
        # Where the records really are, which a lying header does not move.
        self.stride = trace.stride_for(capacity)
        self.path = Path(tempfile.mkstemp(dir="/dev/shm", suffix="-ram")[1])
        self.buffer = bytearray((TRACE_PA - RAM_BASE) + REGION_SIZE)
        self.offset = TRACE_PA - RAM_BASE
        trace.format_region(
            self.buffer, self.offset,
            rings=rings, capacity=capacity, freq_hz=62_500_000, early=early,
            magic=magic, version=version, stride=stride, header_rings=header_rings,
        )
        self.heads = [0] * rings
        self.flush()

    def ring_base(self, ring: int) -> int:
        return self.offset + L["NOVA_TRACE_HEADER_SIZE"] + ring * self.stride

    def emit(self, ring: int, ts: int, code: int, cpu: int = 0, a: int = 0, b: int = 0, c: int = 0) -> None:
        index = self.heads[ring]
        at = self.ring_base(ring) + L["NOVA_TRACE_RECORDS_OFF"] + (index % self.capacity) * L["NOVA_TRACE_REC_SIZE"]
        trace.pack_into(self.buffer, at, trace.Record(ts, code, cpu, a, b, c))
        self.heads[ring] = index + 1
        struct.pack_into("<Q", self.buffer, self.ring_base(ring), self.heads[ring])

    def reformat(self) -> None:
        """What a restart looks like to a reader holding a stale cursor."""
        self.heads = [0] * self.rings
        for ring in range(self.rings):
            struct.pack_into("<Q", self.buffer, self.ring_base(ring), 0)

    def flush(self) -> None:
        self.path.write_bytes(bytes(self.buffer))

    def reader(self) -> trace.TraceReader:
        self.flush()
        return trace.TraceReader(self.path, RAM_BASE, TRACE_PA, REGION_SIZE)

    def cleanup(self) -> None:
        self.path.unlink(missing_ok=True)


BIND = events.BY_ID["vgic.bind"].code
TRAP = events.BY_ID["trap"].code


class GeometryTest(unittest.TestCase):
    def test_the_region_describes_itself(self):
        region = Region(rings=2)
        self.addCleanup(region.cleanup)
        reader = region.reader()
        self.addCleanup(reader.close)
        self.assertEqual(reader.geometry.rings, 2)
        self.assertEqual(reader.geometry.capacity, CAPACITY)
        # ts -> seconds has one source and it travels with the geometry.
        self.assertEqual(reader.geometry.freq_hz, 62_500_000)


    def test_a_wrong_magic_is_refused_not_decoded(self):
        """Decoding anyway would turn a version skew into events that
        look plausible and are not."""
        region = Region(magic=0xDEAD)
        self.addCleanup(region.cleanup)
        with self.assertRaises(trace.NotFormatted):
            region.reader()

    def test_a_future_version_is_refused(self):
        region = Region(version=trace.VERSION + 1)
        self.addCleanup(region.cleanup)
        with self.assertRaises(trace.NotFormatted):
            region.reader()

    def test_a_ring_count_past_the_abi_ceiling_is_refused(self):
        """The depth now arrives entirely from the header, so the header
        is what a reader indexes with — and MAX_RINGS is the one bound
        it can still hold that number to."""
        region = Region(header_rings=trace.MAX_RINGS + 1)
        self.addCleanup(region.cleanup)
        with self.assertRaises(trace.NotFormatted):
            region.reader()

    def test_a_stride_that_disagrees_with_the_capacity_is_refused(self):
        """Two numbers describing one layout. Believing the stride and
        indexing with the capacity would read every ring but the first
        at an offset nothing was written to — records that are all
        present, all decodable, and all wrong."""
        region = Region(stride=trace.stride_for(CAPACITY) + 32)
        self.addCleanup(region.cleanup)
        with self.assertRaises(trace.NotFormatted):
            region.reader()

    def test_a_short_backend_is_refused(self):
        region = Region()
        self.addCleanup(region.cleanup)
        region.path.write_bytes(bytes(64))
        with self.assertRaises(trace.NotFormatted):
            trace.TraceReader(region.path, RAM_BASE, TRACE_PA, REGION_SIZE)


class DrainTest(unittest.TestCase):
    def setUp(self):
        self.region = Region(rings=2)
        self.addCleanup(self.region.cleanup)

    def test_records_round_trip_with_their_arguments(self):
        self.region.emit(0, ts=100, code=BIND, cpu=0, a=0, b=37 | (37 << 32), c=3)
        reader = self.region.reader()
        self.addCleanup(reader.close)

        records = reader.drain()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].event, "vgic.bind")
        self.assertEqual(records[0].edge, "post")
        decoded = trace.decode(records[0])
        # The firmware packs both INTIDs into one word, physical high;
        # unpacking here keeps that a detail of the EL2-to-bridge wire.
        self.assertEqual(decoded["vintid"], 37)
        self.assertEqual(decoded["pintid"], 37)
        self.assertEqual(decoded["generation"], 3)

    def test_a_second_drain_returns_only_what_is_new(self):
        self.region.emit(0, ts=1, code=TRAP)
        reader = self.region.reader()
        self.addCleanup(reader.close)
        self.assertEqual(len(reader.drain()), 1)
        self.assertEqual(reader.drain(), [])

        self.region.emit(0, ts=2, code=TRAP)
        self.region.flush()
        records = reader.drain()
        self.assertEqual([record.ts for record in records], [2])
        self.assertEqual(trace.dropped_in(records), 0)

    def test_rings_merge_by_timestamp(self):
        """CNTPCT is common to every PE, so the merge is the machine's
        real order — the thing a sampled layer cannot supply at any
        rate."""
        self.region.emit(0, ts=30, code=TRAP, cpu=0)
        self.region.emit(1, ts=10, code=TRAP, cpu=1)
        self.region.emit(0, ts=20, code=TRAP, cpu=0)
        reader = self.region.reader()
        self.addCleanup(reader.close)

        records = reader.drain()
        self.assertEqual([record.ts for record in records], [10, 20, 30])
        self.assertEqual([record.cpu for record in records], [1, 0, 0])

    def test_a_lapped_writer_is_counted_not_hidden(self):
        for index in range(CAPACITY * 3):
            self.region.emit(0, ts=index, code=TRAP)
        reader = self.region.reader()
        self.addCleanup(reader.close)

        records = reader.drain()
        kept = [record for record in records if record.code != trace.GAP_CODE]
        lost = trace.dropped_in(records)
        # One short of the capacity. The slot the writer would fill next
        # is the one holding the oldest record, and nothing in the
        # region distinguishes "head is resting here" from "the writer
        # is halfway through this slot" — so that record is given up
        # rather than handed out as two events spliced together.
        self.assertEqual(len(kept), CAPACITY - 1)
        self.assertEqual(lost, CAPACITY * 2 + 1)
        # Every record between the cursor and the head is accounted for.
        self.assertEqual(len(kept) + lost, CAPACITY * 3)
        self.assertEqual(kept[0].ts, CAPACITY * 2 + 1)

    def test_a_reformatted_region_restarts_rather_than_reporting_negative_loss(self):
        """A restart that reuses the backing file rewinds head to zero.
        Subtracting a stale cursor from it would report a loss of minus
        several thousand."""
        self.region.emit(0, ts=1, code=TRAP)
        self.region.emit(0, ts=2, code=TRAP)
        reader = self.region.reader()
        self.addCleanup(reader.close)
        reader.drain()

        self.region.reformat()
        self.region.emit(0, ts=9, code=TRAP)
        self.region.flush()
        records = reader.drain()
        self.assertEqual([record.ts for record in records], [9])
        self.assertGreaterEqual(trace.dropped_in(records), 0)


class GapTest(unittest.TestCase):
    """A hole in the stream, as a record in the stream.

    The count alone was true and useless: a reader shown two marks with
    eight thousand records missing between them reads a causal chain
    that did not happen. These hold the position, not just the number.
    """

    def setUp(self):
        self.region = Region(rings=2)
        self.addCleanup(self.region.cleanup)

    def gaps(self, records):
        return [record for record in records if record.code == trace.GAP_CODE]

    def test_a_lapped_ring_yields_a_gap_at_the_records_it_swallowed(self):
        self.region.emit(0, ts=10, code=TRAP)
        reader = self.region.reader()
        self.addCleanup(reader.close)
        reader.drain()  # cursor now sits just past ts=10

        for index in range(CAPACITY * 2):
            self.region.emit(0, ts=100 + index, code=TRAP)
        self.region.flush()
        records = reader.drain()

        gap = self.gaps(records)
        self.assertEqual(len(gap), 1)
        # It opens where the last record handed out sat and closes on
        # the first that survived, so the hole occupies exactly the
        # stretch nothing can be said about.
        survivors = [record for record in records if record.code != trace.GAP_CODE]
        self.assertEqual(gap[0].b, 10)
        self.assertEqual(gap[0].ts, survivors[0].ts)
        self.assertEqual(gap[0].cpu, 0)
        self.assertEqual(gap[0].a + len(survivors), CAPACITY * 2)

    def test_the_gap_sorts_into_place_rather_than_onto_the_end(self):
        """A merged stream has one order, and a hole belongs in it. Ahead
        of the records it precedes, behind the other ring's earlier
        ones."""
        self.region.emit(1, ts=5, code=TRAP, cpu=1)
        for index in range(CAPACITY * 2):
            self.region.emit(0, ts=100 + index, code=TRAP)
        reader = self.region.reader()
        self.addCleanup(reader.close)

        stamps = [record.ts for record in reader.drain()]
        self.assertEqual(stamps, sorted(stamps))
        self.assertEqual(stamps[0], 5)

    def test_a_drain_that_recovers_nothing_carries_the_count_forward(self):
        """With no surviving record there is no timestamp to close a
        hole on. Placing it anyway would put a loss at a moment nothing
        happened; dropping it would lose the one number that says the
        history is incomplete."""
        region = Region(rings=1)
        self.addCleanup(region.cleanup)
        region.emit(0, ts=10, code=TRAP)
        reader = region.reader()
        self.addCleanup(reader.close)
        reader.drain()

        region.emit(0, ts=20, code=TRAP)
        region.flush()
        # The writer laps the entire ring between the two head reads.
        # That is the only way a copy comes back with nothing to show
        # for itself, and the race the re-read exists to detect — so it
        # is scripted here rather than waited for.
        heads = iter([2, 2 + CAPACITY * 3])
        original, reader._head = reader._head, lambda _ring: next(heads)
        self.assertEqual(reader.drain(), [])
        reader._head = original

        region.emit(0, ts=30, code=TRAP)
        region.flush()
        records = reader.drain()
        self.assertEqual(trace.dropped_in(records), 1)
        gap = self.gaps(records)[0]
        self.assertEqual(gap.ts, 30)  # closed by the record that arrived
        self.assertEqual(gap.b, 10)  # opened where the stream last was

    def test_the_pre_placement_drops_become_a_gap_with_no_start(self):
        """They predate the region, so no cursor arithmetic recovers
        them and nothing precedes them to open the hole at. Published
        beside the geometry *and* placed on the axis: a count on a
        status line is not somewhere a reader looking at a boot will
        find it."""
        region = Region(early=17)
        self.addCleanup(region.cleanup)
        region.emit(0, ts=42, code=TRAP)
        reader = region.reader()
        self.addCleanup(reader.close)

        records = reader.drain()
        gap = self.gaps(records)[0]
        self.assertEqual(gap.a, 17)
        self.assertEqual(gap.b, 0)  # no near end: nothing came before
        self.assertEqual(gap.ts, 42)
        # Once folded in, it is not folded in again.
        region.emit(0, ts=43, code=TRAP)
        region.flush()
        self.assertEqual(self.gaps(reader.drain()), [])

    def test_a_gap_lights_no_path(self):
        """The grade rule in paths.py, at its sharpest: a stretch nobody
        watched is evidence for nothing at all."""
        self.assertEqual(events.BY_ID["trace.gap"].edge, "")
        summary = trace.summarise(
            [trace.Record(ts=9, code=trace.GAP_CODE, cpu=0, a=5, b=1, c=0)]
        )
        self.assertEqual(summary["edges"], {})
        self.assertEqual(summary["dropped"], 5)

    def test_a_gap_decodes_as_a_width_not_a_counter_value(self):
        decoded = trace.decode(trace.Record(ts=900, code=trace.GAP_CODE, cpu=0, a=5, b=400, c=0))
        self.assertEqual(decoded["count"], 5)
        self.assertEqual(decoded["ticks"], 500)


class BoundedDrainTest(unittest.TestCase):
    """A drain may cost only so much of the caller's turn. What it costs
    is the backlog, and the backlog is how long the last turn took, so
    without a cap the two feed each other."""

    def interleaved(self, per_ring: int = 24):
        """Two rings whose records alternate in time, so a batch that
        respected only per-ring counts would hand them over jumbled."""
        region = Region(rings=2, capacity=64)
        self.addCleanup(region.cleanup)
        for index in range(per_ring):
            region.emit(0, ts=2 * index, code=TRAP, cpu=0)
            region.emit(1, ts=2 * index + 1, code=TRAP, cpu=1)
        return region

    def test_chunked_drains_give_what_one_drain_would_have(self):
        """The cap changes when records arrive, never which ones or in
        what order."""
        whole = self.interleaved()
        reader = whole.reader()
        self.addCleanup(reader.close)
        expected = [(record.ts, record.cpu) for record in reader.drain()]
        reader.close()

        region = self.interleaved()
        chunked = region.reader()
        self.addCleanup(chunked.close)
        got = []
        while len(got) < len(expected):
            batch = chunked.drain(limit=7)
            self.assertTrue(batch, "a bounded drain must always advance")
            got += [(record.ts, record.cpu) for record in batch]
        self.assertEqual(got, expected)

    def test_no_record_left_behind_is_older_than_one_handed_over(self):
        """The batch ends at a moment, not at a count. A ring stopped
        short would otherwise leave records the next batch has to insert
        underneath ones already held."""
        region = self.interleaved()
        reader = region.reader()
        self.addCleanup(reader.close)
        newest = max(record.ts for record in reader.drain(limit=9))
        oldest_left = min(record.ts for record in reader.drain())
        self.assertGreaterEqual(oldest_left, newest)

    def test_an_allowance_a_quiet_ring_did_not_want_goes_to_a_busy_one(self):
        region = Region(rings=2, capacity=64)
        self.addCleanup(region.cleanup)
        region.emit(1, ts=0, code=TRAP, cpu=1)
        for index in range(40):
            region.emit(0, ts=index + 1, code=TRAP, cpu=0)
        reader = region.reader()
        self.addCleanup(reader.close)
        # Ring 1 wants one of the twenty; the split must not strand the
        # other nine on a ring with nothing to give.
        self.assertEqual(len(reader.drain(limit=20)), 20)


class BudgetTest(unittest.TestCase):
    """The ring depth is a latency budget, so both of its terms — the
    peak fill and the stall between looks — are measured per run."""

    # A round counter so a rate reads straight off the spacing: one tick
    # per microsecond, so records `FREQ // rate` apart arrive at `rate`.
    FREQ = 1_000_000

    def budget(self, capacity: int = 1000):
        return trace.Budget(capacity, self.FREQ)

    def records(self, count: int, cpu: int = 0, rate: int = 200):
        """A ring's worth of records produced at `rate` per second."""
        step = self.FREQ // rate
        return [trace.Record(ts=index * step, code=TRAP, cpu=cpu, a=0, b=0, c=0)
                for index in range(count)]

    def test_the_horizon_falls_out_of_the_fastest_fill_seen(self):
        budget = self.budget()
        budget.looked([], 0.0)
        budget.looked(self.records(200, rate=200), 0.5)  # one window's worth
        self.assertEqual(budget.as_dict()["peak_rate"], 200)
        self.assertAlmostEqual(budget.horizon_seconds, 5.0)

    def test_the_fill_is_read_off_the_stamps_not_off_the_turn(self):
        """A bounded drain working through a backlog hands over a second
        of records inside a few milliseconds. Dividing by the turn would
        call that a fill rate the firmware never reached, and shrink the
        horizon on evidence about the reader."""
        budget = self.budget()
        budget.looked([], 0.0)
        # One second of production at 200/s, collected in four ms.
        budget.looked(self.records(200, rate=200), 0.004)
        self.assertEqual(budget.as_dict()["peak_rate"], 200)

    def test_a_burst_shorter_than_the_window_is_not_a_rate(self):
        """The declaration a board is sized against is per second, so
        the measurement is too. The densest microsecond of a boot runs
        at hundreds of times the busiest second, and sizing a ring
        against it would reserve for a burst no depth has to survive."""
        budget = self.budget()
        budget.looked([], 0.0)
        budget.looked(self.records(50, rate=100_000), 0.001)  # half a millisecond
        self.assertEqual(budget.as_dict()["peak_rate"], 0)

    def test_the_rate_is_per_ring_not_across_them(self):
        """The depth is per ring, so a total across four of them would
        claim a horizon four times shorter than the real one."""
        budget = self.budget()
        budget.looked([], 0.0)
        both = self.records(100, cpu=0, rate=100) + self.records(100, cpu=1, rate=100)
        budget.looked(both, 1.0)
        self.assertEqual(budget.as_dict()["peak_rate"], 100)

    def test_a_gap_record_is_not_a_fill(self):
        """It stands for records that never reached the reader, on a
        stamp the reader chose; counting it would inflate the rate
        exactly when the ring was already losing."""
        budget = self.budget()
        budget.looked([], 0.0)
        made = self.records(200, rate=200)
        loss = trace.Record(ts=made[-1].ts, code=trace.GAP_CODE,
                            cpu=0, a=99999, b=0, c=0)
        budget.looked([*made, loss], 1.0)
        self.assertEqual(budget.as_dict()["peak_rate"], 200)

    def test_the_stall_is_measured_between_looks_not_between_finds(self):
        """A ring that was empty was never at risk. Counting an idle
        stretch as a stall makes the worst case a measure of how quiet
        the run was."""
        budget = self.budget()
        budget.looked(self.records(1), 0.0)
        budget.looked([], 2.0)  # a long look that found nothing
        budget.looked(self.records(1), 2.1)
        self.assertAlmostEqual(budget.as_dict()["worst_gap_ms"], 2000.0)

    def test_the_crossing_is_reported_rather_than_left_to_be_noticed(self):
        budget = self.budget(capacity=100)
        budget.looked([], 0.0)
        budget.looked(self.records(1000, rate=1000), 0.1)  # horizon 100 ms
        self.assertAlmostEqual(budget.as_dict()["horizon_ms"], 100.0)
        self.assertFalse(budget.overrun)

        budget.looked(self.records(1), 0.5)  # 400 ms without a look
        self.assertTrue(budget.overrun)
        self.assertTrue(budget.as_dict()["overrun"])

    def test_an_unmeasured_budget_is_zero_and_not_unlimited(self):
        budget = self.budget()
        budget.looked([], 0.0)
        budget.looked([], 1.0)
        self.assertEqual(budget.horizon_seconds, 0.0)
        # Nothing has been seen, so nothing has been broken either.
        self.assertFalse(budget.overrun)

    def attributed(self, capacity=1000):
        """A budget whose two attribution clocks are driven by hand."""
        clocks = {"cpu": 0.0, "gc": 0.0}
        budget = trace.Budget(
            capacity, self.FREQ,
            cpu_clock=lambda: clocks["cpu"], gc_clock=lambda: clocks["gc"],
        )
        return budget, clocks

    def test_the_stall_is_split_into_what_the_process_spent_and_did_not(self):
        """"QEMU contention or Python GC" is answered by subtraction, not
        by guessing: a stall the process was running through is its own,
        and one it was absent for was taken from it."""
        budget, clocks = self.attributed()
        budget.looked([], 0.0)
        clocks["cpu"] += 0.004  # 4 ms of CPU inside a 500 ms wall stall
        budget.looked([], 0.5)
        state = budget.as_dict()
        self.assertAlmostEqual(state["worst_gap_ms"], 500.0)
        self.assertAlmostEqual(state["worst_cpu_ms"], 4.0)
        self.assertAlmostEqual(state["worst_gc_ms"], 0.0)

    def test_a_collection_inside_the_stall_is_named_as_one(self):
        budget, clocks = self.attributed()
        budget.looked([], 0.0)
        clocks["cpu"] += 0.180
        clocks["gc"] += 0.175  # nearly all of the CPU was collecting
        budget.looked([], 0.2)
        state = budget.as_dict()
        self.assertAlmostEqual(state["worst_cpu_ms"], 180.0)
        self.assertAlmostEqual(state["worst_gc_ms"], 175.0)

    def test_the_composition_belongs_to_the_stall_it_explains(self):
        """A later, smaller interval must not overwrite the breakdown of
        the worst one — the two would then describe different moments."""
        budget, clocks = self.attributed()
        budget.looked([], 0.0)
        clocks["cpu"] += 0.004
        budget.looked([], 0.5)  # the worst
        clocks["cpu"] += 0.300
        budget.looked([], 0.6)  # busier, but shorter
        state = budget.as_dict()
        self.assertAlmostEqual(state["worst_gap_ms"], 500.0)
        self.assertAlmostEqual(state["worst_cpu_ms"], 4.0)

    def test_a_band_appears_only_where_there_is_data(self):
        """No table of edges travels with the histogram, so nothing has
        to be revised when the loop's cadence or the depth changes."""
        budget = self.budget()
        for at in (0.0, 0.005, 0.010, 0.674):  # 5 ms, 5 ms, then 664 ms
            budget.looked([], at)
        self.assertEqual(budget.as_dict()["gaps"], {"4": 2, "512": 1})

    def test_the_band_is_the_power_of_two_below_the_interval(self):
        budget = self.budget()
        at = 0.0
        budget.looked([], at)  # the first look is the baseline, not an interval
        for ms in (1.0, 1.9, 2.0, 3.9, 4.0):
            at += ms / 1000
            budget.looked([], at)
        self.assertEqual(budget.as_dict()["gaps"], {"1": 2, "2": 2, "4": 1})

    def test_every_look_is_counted_so_an_outlier_has_a_denominator(self):
        """The worst gap alone cannot say whether it happened once or
        happens all the time; the band the loop's own tick lands in is
        what the outlier is read against."""
        budget = self.budget()
        at = 0.0
        budget.looked([], at)
        for _ in range(100):
            at += 0.0002  # 200 us — under a millisecond, band zero
            budget.looked([], at)
        at += 0.664
        budget.looked([], at)
        gaps = budget.as_dict()["gaps"]
        self.assertEqual(gaps, {"0": 100, "512": 1})
        self.assertEqual(sum(gaps.values()), 101)


class SummaryTest(unittest.TestCase):
    def test_the_wire_carries_counts_and_the_last_of_each(self):
        """Bounded by the number of paths, not by the event rate: a few
        thousand a second is nothing to the bridge and a great deal to a
        browser."""
        records = [
            trace.Record(ts=1, code=TRAP, cpu=0, a=0x16, b=0, c=0),
            trace.Record(ts=2, code=TRAP, cpu=0, a=0x16, b=0, c=0),
            trace.Record(ts=3, code=BIND, cpu=0, a=0, b=37 | (37 << 32), c=4),
        ]
        summary = trace.summarise(records)
        self.assertEqual(summary["edges"], {"trap": 2, "post": 1})
        self.assertEqual(summary["last"]["post"]["pintid"], 37)
        self.assertEqual(summary["last"]["post"]["generation"], 4)

    def test_an_unknown_code_is_skipped_rather_than_invented(self):
        summary = trace.summarise([trace.Record(ts=1, code=999, cpu=0, a=0, b=0, c=0)])
        self.assertEqual(summary["edges"], {})


class CatalogueTest(unittest.TestCase):
    def test_every_event_carries_the_firmware_code(self):
        """One catalogue, two consumers. A hook the firmware numbers and
        this file does not would drain as an unknown record."""
        for event in events.EVENTS:
            with self.subTest(event=event.id):
                self.assertGreater(event.code, 0)

    def test_codes_are_unique(self):
        codes = [event.code for event in events.EVENTS]
        self.assertEqual(len(set(codes)), len(codes))

    def test_every_code_the_firmware_defines_is_catalogued(self):
        """The other direction, and the one that fails quietly: a hook
        added to the ABI header and not here writes records that arrive
        as a number nothing can name, and summarise() skips them without
        a word. The codes are read as a family, so the header is the
        only list — this checks nobody added to it alone."""
        for name, code in events._CODES.items():
            with self.subTest(code=name):
                self.assertIn(code, events.BY_CODE, f"{name} has no catalogue entry")

    def test_the_two_code_bands_cannot_collide(self):
        """The host writes into the same stream, and the only thing
        keeping its codes from meaning a firmware hook is the distance
        between the two numberings."""
        base = abi.read_define(abi.TRACE_RING, "NOVA_TRACE_HOST_CODE_BASE")
        self.assertTrue(events._CODES, "the firmware family must not read as empty")
        self.assertLess(max(events._CODES.values()), base)
        for name, code in events._HOST_CODES.items():
            with self.subTest(code=name):
                self.assertGreaterEqual(code, base)
                self.assertLessEqual(code, 0xFFFF)  # the record's type is a u16

    def test_every_host_code_is_catalogued_too(self):
        """The same quiet failure as the firmware side: a code written
        into the stream and named nowhere arrives as a number the UI
        skips without a word."""
        for name, code in events._HOST_CODES.items():
            with self.subTest(code=name):
                self.assertIn(code, events.BY_CODE, f"{name} has no catalogue entry")

    def test_only_the_entries_with_a_symbol_are_offered_as_stops(self):
        """A record kind the host writes has no instruction to break on,
        so arming it would be a breakpoint that can never be hit."""
        self.assertNotIn("trace.gap", {event.id for event in events.STOPS})
        for event in events.STOPS:
            with self.subTest(event=event.id):
                self.assertTrue(event.symbol)
        for entry in events.catalogue():
            if entry["id"] == "trace.gap":
                self.assertFalse(entry["stop"])
                self.assertTrue(entry["span"])
