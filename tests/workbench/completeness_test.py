"""Whether a run's totals are all of it, and how the answer is reached.

A frequency is only a measurement if nothing was missed, and a trace ring
can miss in three ways: before the reader attached, when the writer laps
it, and at the end, when the producer dies with counts still held. The
first two already become gap records as soon as a surviving record can
carry one. The third could not, because no record follows — which is what
`flush_held` and `drain_to_head` are for.

The fake region is `trace_test`'s, so a fixture here cannot describe a
layout the reader does not read.
"""

from __future__ import annotations

import asyncio  # noqa: TID251 — the boundaries under test are loop callbacks
import time
import unittest
from unittest import mock

from novakit.services.workbench import events, server, trace, trace_drain
from novakit.services.workbench.session import Deps, Prepared, Session, Target
from tests.workbench.session_test import FakeLive, deps_for, scenario, store
from tests.workbench.trace_test import BIND, Region


class LedgerTest(unittest.TestCase):
    """What the totals add up to, and what makes them incomplete."""

    def ledger(self, *records: trace.Record) -> trace.RunLedger:
        book = trace.RunLedger()
        book.consume(list(records))
        return book

    def gap(self, count: int, *, ts: int = 1, ring: int = 0, opened: int = 0) -> trace.Record:
        return trace.Record(ts=ts, code=trace.GAP_CODE, cpu=ring, a=count, b=opened, c=0)

    def event(self, ts: int = 1) -> trace.Record:
        return trace.Record(ts=ts, code=BIND, cpu=0, a=0, b=0, c=0)

    def test_a_run_with_nothing_missed_and_a_drained_tail_is_complete(self):
        totals = self.ledger(self.event(), self.event()).seal(producer_dead=True, tail_drained=True)
        self.assertEqual(totals.lost, 0)
        self.assertTrue(totals.complete)
        self.assertEqual(sum(totals.events.values()), 2)

    def test_every_kind_of_loss_adds_to_one_count(self):
        """Early, lapped and terminal holes are summed, never told apart.

        They can be written identically in the raw stream, so a ledger
        that claimed to separate them would claim more than the records
        say — and completeness does not need them separated.
        """
        totals = self.ledger(
            self.gap(3, opened=0),  # early: nothing precedes it
            self.event(),
            self.gap(5, ring=1, opened=7),  # a lapped ring
            self.gap(2, ring=1, opened=9),  # the terminal flush
        ).seal(producer_dead=True, tail_drained=True)
        self.assertEqual(totals.lost, 10)
        self.assertFalse(totals.complete)

    def test_a_gap_is_not_counted_as_an_event(self):
        totals = self.ledger(self.gap(4), self.event()).seal(producer_dead=True, tail_drained=True)
        self.assertEqual(sum(totals.events.values()), 1)

    def test_an_undrained_tail_is_incomplete_even_with_nothing_lost(self):
        self.assertFalse(self.ledger(self.event()).seal(producer_dead=True, tail_drained=False).complete)

    def test_the_two_ways_of_not_draining_stay_told_apart(self):
        """A stop that failed and a deadline that passed are both
        incomplete, and the fix for one is not the fix for the other."""
        stopped = self.ledger(self.event()).seal(producer_dead=False, tail_drained=False)
        timed_out = self.ledger(self.event()).seal(producer_dead=True, tail_drained=False)
        self.assertFalse(stopped.complete)
        self.assertFalse(timed_out.complete)
        self.assertNotEqual(stopped.raw(), timed_out.raw())

    def test_the_raw_facts_are_the_ones_the_records_cannot_answer(self):
        """What a recording has to carry for its totals to be re-derived."""
        totals = self.ledger(self.event()).seal(producer_dead=True, tail_drained=True)
        self.assertEqual(set(totals.raw()), set(trace.RunTotals.RAW))
        self.assertNotIn("lost", totals.raw())
        self.assertNotIn("events", totals.raw())

    def test_an_image_without_a_ring_is_absent_rather_than_incomplete(self):
        totals = self.ledger().seal(producer_dead=True, tail_drained=False, absent=True)
        self.assertTrue(totals.absent)
        self.assertFalse(totals.complete)


class CountingDimensionTest(unittest.TestCase):
    """Which word a run's totals break an event down by, and who decides.

    The catalogue does. A branch here naming "trap" and reading `a` would
    keep answering after the catalogue moved the EC to another word, so
    every expectation below is read out of the catalogue itself.
    """

    def record(self, code: int) -> trace.Record:
        """Distinguishable words, so the wrong one cannot pass as the right one."""
        return trace.Record(ts=1, code=code, cpu=0, a=11, b=22, c=33)

    def test_the_word_counted_is_the_one_the_catalogue_names(self):
        grouped = [event for event in events.EVENTS if event.code and event.group]
        self.assertTrue(grouped, "no event declares a breakdown: the dimension is gone")
        for event in grouped:
            with self.subTest(event=event.id):
                record = self.record(event.code)
                expected = (record.a, record.b, record.c)[event.group_index]
                self.assertEqual(trace.key_of(record), (event.code, expected))

    def test_an_event_with_no_breakdown_counts_under_its_code_alone(self):
        plain = next(event for event in events.EVENTS if event.code and not event.group)
        self.assertEqual(trace.key_of(self.record(plain.code)), (plain.code, 0))

    def test_the_breakdown_map_is_exactly_what_the_catalogue_declares(self):
        self.assertEqual(
            set(trace._GROUP_WORD),
            {event.code for event in events.EVENTS if event.code and event.group},
        )

    def test_the_two_breakdowns_the_audit_asked_for_are_declared(self):
        """A trap count is a number; a trap count per EC is a cause."""
        self.assertEqual(events.BY_ID["trap"].group, "ec")
        self.assertEqual(events.BY_ID["gic.ack"].group, "intid")

    def test_one_code_with_two_argument_values_is_two_totals(self):
        trap = events.BY_ID["trap"]
        book = trace.RunLedger()
        book.consume(
            [
                trace.Record(ts=1, code=trap.code, cpu=0, a=0x16, b=0, c=0),
                trace.Record(ts=2, code=trap.code, cpu=0, a=0x16, b=0, c=0),
                trace.Record(ts=3, code=trap.code, cpu=0, a=0x24, b=0, c=0),
            ]
        )
        totals = book.seal(producer_dead=True, tail_drained=True)
        self.assertEqual(totals.events, {(trap.code, 0x16): 2, (trap.code, 0x24): 1})
        self.assertEqual(totals.as_dict()["events"], {f"{trap.code}:22": 2, f"{trap.code}:36": 1})


class FlushHeldTest(unittest.TestCase):
    """The losses no record could carry, emitted once at the end."""

    def setUp(self):
        self.region = Region(rings=2)
        self.addCleanup(self.region.cleanup)

    def lap(self, reader: trace.TraceReader, ring: int) -> None:
        """Write past the ring's depth so the drain recovers nothing."""
        for index in range(self.region.capacity * 2):
            self.region.emit(ring, ts=100 + index, code=BIND)
        self.region.flush()
        reader.drain()

    def test_a_held_count_becomes_a_gap_record_at_the_end(self):
        reader = self.region.reader()
        self.addCleanup(reader.close)
        # A ring that lapped with nothing surviving holds its count: the
        # drain has no timestamp to close the hole on.
        self.region.emit(0, ts=10, code=BIND)
        self.region.flush()
        reader.drain()
        with mock.patch.object(trace.TraceReader, "_oldest_intact", return_value=1 << 40):
            self.lap(reader, 0)
        self.assertGreater(reader.held_losses(), 0)

        held = reader.flush_held()
        self.assertTrue(held)
        self.assertTrue(all(record.code == trace.GAP_CODE for record in held))
        self.assertEqual(reader.held_losses(), 0, "flushing twice would double-count")
        self.assertEqual(reader.flush_held(), [])

    def test_pre_placement_drops_are_flushed_even_with_no_records_at_all(self):
        region = Region(rings=1, early=6)
        self.addCleanup(region.cleanup)
        reader = region.reader()
        self.addCleanup(reader.close)
        self.assertEqual(reader.held_losses(), 6)
        held = reader.flush_held()
        self.assertEqual([record.a for record in held], [6])

    def test_behind_says_whether_a_cursor_still_trails_the_snapshot(self):
        reader = self.region.reader()
        self.addCleanup(reader.close)
        self.region.emit(0, ts=1, code=BIND)
        self.region.flush()
        heads = reader.snapshot_heads()
        self.assertTrue(reader.behind(heads))
        reader.drain()
        self.assertFalse(reader.behind(heads))


class _Drain(trace_drain.TraceDrain):
    """A drain wired to a region and nothing else.

    Only the collaborators `drain_to_head` touches are real; the rest
    would drag a whole bridge in to answer a question about a ring.
    """

    def __init__(self, reader: trace.TraceReader | None, *, has_tracing: bool = True):
        self.tracer = reader
        self.ledger = trace.RunLedger()
        self.consumed: list[trace.Record] = []
        self.attached = 0
        self._has_tracing = has_tracing

    def _image_has_tracing(self) -> bool:
        return self._has_tracing

    def attach(self) -> bool:
        self.attached += 1
        return self.tracer is not None

    def consume(self, records: list[trace.Record]) -> None:
        self.consumed.extend(records)
        self.ledger.consume(records)

    def pump(self) -> bool:
        if self.tracer is None:
            return False
        self.consume(self.tracer.drain())
        return bool(self.tracer.pending())


class DrainToHeadTest(unittest.TestCase):
    """What the end of a run seals, and what it refuses to claim."""

    def setUp(self):
        self.region = Region(rings=1)
        self.addCleanup(self.region.cleanup)

    def test_an_unconfirmed_stop_seals_incomplete_without_reading(self):
        """The heads only stop moving when the writer does."""
        reader = self.region.reader()
        self.addCleanup(reader.close)
        drain = _Drain(reader)
        totals = drain.drain_to_head(producer_dead=False)
        self.assertFalse(totals.complete)
        self.assertFalse(totals.tail_drained)
        self.assertEqual(drain.consumed, [])

    def test_a_clean_tail_drains_and_seals_complete(self):
        self.region.emit(0, ts=1, code=BIND)
        self.region.emit(0, ts=2, code=BIND)
        self.region.flush()
        reader = self.region.reader()
        self.addCleanup(reader.close)
        totals = _Drain(reader).drain_to_head(producer_dead=True)
        self.assertTrue(totals.tail_drained)
        self.assertTrue(totals.complete)
        self.assertEqual(sum(totals.events.values()), 2)

    def test_a_held_loss_reaches_the_ledger_through_the_one_consume_path(self):
        """Recovered in full, and still not a complete run.

        tail_drained says every raw fact was collected; complete says
        none was missing. A terminal gap is the case where those differ.
        """
        reader = self.region.reader()
        self.addCleanup(reader.close)
        drain = _Drain(reader)
        # The state a lapped ring leaves behind when no record survived to
        # close the hole. Set directly: arranging it through the writer
        # would test the drain's lapping arithmetic, which trace_test owns.
        reader._pending[0] = 4
        totals = drain.drain_to_head(producer_dead=True)
        self.assertTrue(totals.tail_drained)
        self.assertEqual(totals.lost, 4)
        self.assertFalse(totals.complete)
        self.assertTrue(any(record.code == trace.GAP_CODE for record in drain.consumed))

    def test_a_deadline_that_cannot_be_met_seals_incomplete_and_returns(self):
        reader = self.region.reader()
        self.addCleanup(reader.close)
        drain = _Drain(reader)
        # A writer that never stops: behind() is always true, so only the
        # deadline can end the loop.
        with mock.patch.object(trace.TraceReader, "behind", return_value=True):
            started = time.monotonic()
            with mock.patch.object(trace_drain, "TAIL_DEADLINE_S", 0.05):
                totals = drain.drain_to_head(producer_dead=True)
            elapsed = time.monotonic() - started
        self.assertFalse(totals.tail_drained)
        self.assertFalse(totals.complete)
        self.assertLess(elapsed, 2.0, "the deadline bounds the wait")

    def test_an_unattachable_reader_retries_once_then_seals_without_raising(self):
        drain = _Drain(None, has_tracing=True)
        totals = drain.drain_to_head(producer_dead=True)
        self.assertEqual(drain.attached, 1)
        self.assertFalse(totals.absent, "the image has a ring; failing to read it is a loss")
        self.assertFalse(totals.complete)

    def test_an_image_without_tracing_seals_absent_not_incomplete(self):
        totals = _Drain(None, has_tracing=False).drain_to_head(producer_dead=True)
        self.assertTrue(totals.absent)
        self.assertFalse(totals.complete)


class RunBoundaryTest(unittest.IsolatedAsyncioTestCase):
    """Every way a run can end reports it, and it is sealed once.

    Three paths reach the end of a run — the bridge stopping the machine,
    a target replacing it, and the machine exiting on its own — and a
    reader that only heard about one would drain a live ring or none.
    """

    def session_with(self, live) -> tuple[Session, list[tuple[int, bool]]]:
        session = Session(store(), deps_for(live))
        ended: list[tuple[int, bool]] = []
        session.on_run_end = lambda run, dead: ended.append((run, dead))
        return session, ended

    def fresh_machines(self) -> tuple[Session, list[tuple[int, bool]], list[FakeLive]]:
        """A session that launches a new machine each time, as the real one does."""
        machines: list[FakeLive] = []

        def launch(_command):
            live = FakeLive()
            machines.append(live)
            self.addCleanup(live.terminate)
            return live

        def prepare(target: Target) -> Prepared:
            return Prepared(scenario(), {"demo": target.demo})

        session = Session(store(), Deps(prepare=prepare, launch=launch))
        ended: list[tuple[int, bool]] = []
        session.on_run_end = lambda run, dead: ended.append((run, dead))
        return session, ended, machines

    async def running(self, live) -> tuple[Session, list[tuple[int, bool]]]:
        session, ended = self.session_with(live)
        await session.select(Target(demo="10_console_mux"))
        return session, ended

    async def test_stopping_the_bridge_reports_the_end_with_the_confirmation(self):
        live = FakeLive()
        session, ended = await self.running(live)
        self.assertTrue(await session.stop())
        self.assertEqual(ended, [(session.run_id, True)])

    async def test_replacing_the_target_reports_the_run_it_ended(self):
        session, ended, _ = self.fresh_machines()
        await session.select(Target(demo="10_console_mux"))
        first = session.run_id
        await session.select(Target(demo="01_hello"))
        self.assertIn((first, True), ended)
        self.assertNotEqual(session.run_id, first)

    async def test_a_machine_exiting_on_its_own_reports_the_end(self):
        live = FakeLive()
        session, ended = await self.running(live)
        run = session.run_id
        live.peer.close()  # EOF on the pty: the natural-exit path
        for _ in range(200):
            if ended:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(ended, [(run, True)])

    async def test_stopping_a_session_that_never_ran_reports_nothing(self):
        session, ended = self.session_with(FakeLive())
        self.assertTrue(await session.stop(), "no writer to outlive the reader")
        self.assertEqual(ended, [])

    async def test_a_child_that_survives_termination_is_not_confirmed_dead(self):
        live = FakeLive()
        live.terminate = lambda: False  # SIGKILL did not take
        session, ended = await self.running(live)
        self.assertFalse(await session.stop())
        self.assertEqual(ended, [(session.run_id, False)])


class SealOnceTest(unittest.TestCase):
    """A run's totals are decided by whichever boundary arrives first."""

    class _Bridge:
        """Just the part of the bridge that seals — the rest is a machine."""

        def __init__(self, totals: trace.RunTotals):
            self._sealed: dict[int, trace.RunTotals] = {}
            self._totals = totals
            self.drains = 0

        finalize_run = server.Bridge.finalize_run

        @property
        def _trace_service(self):
            bridge = self

            class _Service:
                def drain_to_head(self, _producer_dead: bool) -> trace.RunTotals:
                    bridge.drains += 1
                    return bridge._totals

            return _Service()

        @property
        def store(self):
            class _Store:
                def publish(self, *args, **kwargs) -> None:
                    pass

            return _Store()

    def test_the_second_boundary_is_handed_what_the_first_found(self):
        totals = trace.RunLedger().seal(producer_dead=True, tail_drained=True)
        bridge = self._Bridge(totals)
        first = bridge.finalize_run(3, True)
        second = bridge.finalize_run(3, False)
        self.assertIs(first, second)
        self.assertEqual(bridge.drains, 1, "a run is drained once, not once per boundary")

    def test_a_different_run_is_sealed_on_its_own(self):
        bridge = self._Bridge(trace.RunLedger().seal(producer_dead=True, tail_drained=True))
        bridge.finalize_run(1, True)
        bridge.finalize_run(2, True)
        self.assertEqual(bridge.drains, 2)


if __name__ == "__main__":
    unittest.main()
