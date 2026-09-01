"""Joining what a run did to what the image is, and refusing not to.

Two columns from two places. The static one is read off an ELF and is
proven in `tests/image/elfstruct_test.py`; here the question is the
join — which run may be put beside which image, what the counts add up
to across repeats, and every way the command refuses to answer.

Recordings are built in a temp directory rather than measured, so the
rules hold without QEMU. What a real run produces is the runtime lane's
to check.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from typer.testing import CliRunner

from novakit import cli
from novakit.services import firmperf
from novakit.services.workbench import events, recording, trace

RUNNER = CliRunner()
BIND = events.BY_ID["vgic.bind"].code
TRAP = events.BY_ID["trap"].code


WHOLE = {"producer_dead": True, "tail_drained": True, "absent": False}


def record(root: Path, runs: list[tuple[dict, list[trace.Record]]], *, image: str = "abc") -> None:
    """A set of runs on disk, written the way a measurement writes one.

    One recorder rolling from run to run, because that is what the
    bridge does: a directory per machine, numbered in order.
    """
    recorder = recording.Recorder(root, {"demo": "test", "board": "qemu_virt"})
    for index, (sealed, records) in enumerate(runs, 1):
        recorder.for_run(index)
        recorder.frame(
            {"topic": "topo", "seq": index, "ts": index, "data": {"demo": "test", "image": image}}
        )
        recorder.drained(records)
        recorder.note(**sealed)
    recorder.close()


class Recorded(unittest.TestCase):
    """A directory to write runs into, removed with the test."""

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="nova-perf-")))


class RederivationTest(Recorded):
    """A recording's totals come from its records, every time it is read."""

    def event(self, code: int, arg: int = 0) -> trace.Record:
        return trace.Record(ts=1, code=code, cpu=0, a=arg, b=0, c=0)

    def gap(self, count: int) -> trace.Record:
        return trace.Record(ts=1, code=trace.GAP_CODE, cpu=0, a=count, b=0, c=0)

    def written(self, *, sealed: dict = WHOLE, records=()) -> recording.Recording:
        record(self.root, [(sealed, list(records))])
        return recording.load(self.root / "run-1")

    def test_the_counts_are_the_records_and_nothing_stored(self):
        loaded = self.written(records=[self.event(BIND), self.event(BIND), self.event(TRAP, 0x16)])
        totals = loaded.totals()
        self.assertEqual(totals.events, {(BIND, 0): 2, (TRAP, 0x16): 1})
        self.assertTrue(totals.complete)
        # Nothing derived is on disk, so nothing on disk can disagree.
        stored = json.loads((loaded.directory / "meta.json").read_text())
        self.assertNotIn("events", stored)
        self.assertNotIn("lost", stored)

    def test_reading_the_same_recording_twice_answers_the_same(self):
        loaded = self.written(records=[self.event(BIND)])
        self.assertEqual(loaded.totals().as_dict(), loaded.totals().as_dict())

    def test_a_run_whose_stop_failed_re_derives_as_incomplete(self):
        totals = self.written(
            sealed={"producer_dead": False, "tail_drained": False, "absent": False},
            records=[self.event(BIND)],
        ).totals()
        self.assertFalse(totals.complete)
        self.assertFalse(totals.producer_dead)
        self.assertEqual(sum(totals.events.values()), 1, "its counts are still a floor")

    def test_a_gap_in_the_records_re_derives_as_a_loss(self):
        totals = self.written(records=[self.event(BIND), self.gap(7)]).totals()
        self.assertEqual(totals.lost, 7)
        self.assertFalse(totals.complete)
        self.assertTrue(totals.tail_drained, "the tail was reached; records were still missing")

    def test_a_file_that_ended_cleanly_says_nothing_about_the_trace(self):
        """`complete` in the meta is about the file, and only the file."""
        loaded = self.written(
            sealed={"producer_dead": False, "tail_drained": False, "absent": False}
        )
        self.assertTrue(loaded.meta["complete"], "the recorder closed it")
        self.assertFalse(loaded.totals().complete)


class ReadingASetTest(Recorded):
    """A measurement is repeats, so the runs are read as a set."""

    def runs(self, *counts: int) -> list[recording.Recording]:
        bind = trace.Record(ts=1, code=BIND, cpu=0, a=0, b=0, c=0)
        record(self.root, [(WHOLE, [bind] * count) for count in counts])
        return recording.load_all(self.root)

    def test_every_run_under_a_root_is_read_in_the_order_recorded(self):
        loaded = self.runs(1, 2, 3)
        self.assertEqual([run.directory.name for run in loaded], ["run-1", "run-2", "run-3"])

    def test_a_directory_that_is_itself_one_run_reads_as_a_set_of_one(self):
        self.runs(1)
        self.assertEqual(len(recording.load_all(self.root / "run-1")), 1)

    def test_the_spread_is_over_every_run_and_the_median_is_the_middle(self):
        summary = firmperf._dynamic(self.runs(1, 5, 3))
        row = summary["events"]["vgic.bind"]
        self.assertEqual((row["min"], row["median"], row["max"]), (1, 3, 5))
        self.assertEqual(summary["runs"], 3)
        self.assertEqual(summary["complete"], 3)

    def test_an_even_number_of_runs_reports_a_count_one_of_them_saw(self):
        """Never the average of two: no run produced that number."""
        self.assertEqual(firmperf._dynamic(self.runs(2, 5))["events"]["vgic.bind"]["median"], 2)

    def test_a_run_that_never_fired_an_event_contributes_a_zero(self):
        """Or the spread would describe a different set of runs per row."""
        row = firmperf._dynamic(self.runs(0, 1))["events"]["vgic.bind"]
        self.assertEqual((row["min"], row["max"]), (0, 1))

    def test_a_broken_down_event_is_named_by_the_value_it_counted(self):
        record(self.root, [(WHOLE, [trace.Record(ts=1, code=TRAP, cpu=0, a=0x16, b=0, c=0)])])
        self.assertIn("trap=0x16", firmperf._dynamic(recording.load_all(self.root))["events"])


class JoinTest(Recorded):
    """Which run may be put beside which image."""

    def setUp(self):
        super().setUp()
        self.elf = self.root / "novavisor.elf"
        self.elf.write_bytes(b"an image")
        # Apart from the ELF: the recorder refuses a root that already
        # holds a recording, and would not notice a stray file otherwise.
        self.runs_at = self.root / "runs"
        self.id = firmperf.observe.image_id(self.elf)

    def joined(self, image: str) -> None:
        record(self.runs_at, [(WHOLE, [])], image=image)
        firmperf._joinable(recording.load_all(self.runs_at), self.elf)

    def test_a_run_of_this_image_joins(self):
        self.joined(self.id)

    def test_a_run_of_another_build_is_refused(self):
        with self.assertRaises(firmperf.Mismatch):
            self.joined("f" * 64)

    def test_a_run_that_never_said_which_image_is_refused_too(self):
        """Unknown is not a match, and a table cannot claim a join it lacks."""
        with self.assertRaises(firmperf.Mismatch):
            self.joined("")


class CommandTest(unittest.TestCase):
    """What the command refuses before it runs anything."""

    def invoke(self, *args: str):
        return RUNNER.invoke(cli.app, ["perf", "firmware", *args])

    def test_a_demo_and_a_recording_together_are_refused(self):
        result = self.invoke("--demo", "01_hello", "--recording", ".")
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("pick one", result.output)

    def test_runs_without_a_demo_is_refused(self):
        result = self.invoke("--runs", "3")
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("--runs", result.output)

    def test_a_preset_that_is_not_the_one_the_demo_runs_on_is_refused(self):
        with mock.patch.object(firmperf, "demo_preset", return_value="aarch64-debug"):
            result = self.invoke("--demo", "01_hello", "--preset", "aarch64-release")
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("aarch64-debug", result.output)

    def test_no_run_named_reports_the_image_alone(self):
        with (
            mock.patch.object(firmperf, "measure") as measured,
            mock.patch.object(firmperf.cmake, "resolve_elf", return_value=Path("x.elf")),
            mock.patch.object(
                firmperf.elfstruct, "analyse", side_effect=firmperf.elfstruct.ContractViolation("-")
            ),
        ):
            self.invoke()
        measured.assert_not_called()


if __name__ == "__main__":
    unittest.main()
