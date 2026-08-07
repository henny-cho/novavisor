"""Writing a run down, and getting the same run back.

The claim a recording makes is that it holds what the wire held. These
hold it to that: same envelopes, same order, same records — and more
than a client saw, because the tee sits ahead of the window that drops
frames when a batch overruns.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from novakit.services.workbench import recording, trace  # noqa: E402
from novakit.services.workbench.protocol import (  # noqa: E402
    Clock,
    Envelopes,
    Kind,
    Topic,
)
from novakit.services.workbench.store import FrameWindow, StateStore  # noqa: E402


def records(count: int, first: int = 100) -> list[trace.Record]:
    return [
        trace.Record(ts=first + index, code=1, cpu=index % 2, a=index, b=index * 2, c=index * 3)
        for index in range(count)
    ]


class RoundTripTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="nova-rec-"))

    def tearDown(self):
        for child in self.directory.iterdir():
            child.unlink()
        self.directory.rmdir()

    def test_the_envelopes_come_back_exactly_as_they_went_out(self):
        """Not "equivalent": identical. A replay that reconstructed
        frames would be a second bridge, free to answer differently
        about one run."""
        recorder = recording.Recorder(self.directory, {"demo": "13_linux"})
        sent = []
        for index in range(5):
            frame = {"seq": index, "topic": "life", "kind": "event",
                     "ts": index * 10, "data": {"phase": "x", "n": index}}
            sent.append(frame)
            recorder.frame(frame)
        recorder.close()

        back = recording.load(self.directory)
        self.assertEqual(back.frames, sent)
        self.assertEqual(back.meta["demo"], "13_linux")
        self.assertEqual(back.meta["frames"], 5)

    def test_records_come_back_at_the_firmwares_own_width(self):
        recorder = recording.Recorder(self.directory)
        written = records(7)
        recorder.drained(written)
        recorder.close()

        back = recording.load(self.directory)
        self.assertEqual(back.records, written)
        self.assertEqual(back.meta["records"], 7)

    def test_a_reader_sees_what_a_flush_has_not_reached_yet_as_absent(self):
        """Buffered on purpose: publish() is synchronous and on the
        bridge's only thread, so the disk write belongs to the loop that
        was already waking every 50 ms."""
        recorder = recording.Recorder(self.directory, {})
        recorder.frame({"seq": 1, "topic": "life", "kind": "event", "ts": 0, "data": {}})
        self.assertEqual((self.directory / recording.WIRE).read_text(), "")
        recorder.flush()
        self.assertEqual(len((self.directory / recording.WIRE).read_text().splitlines()), 1)
        recorder.close()

    def test_a_run_killed_mid_line_still_loads(self):
        """An ordinary way for a recording to end. Refusing the whole
        file over its last half-written line would throw away the run
        that was worth recording."""
        recorder = recording.Recorder(self.directory, {})
        recorder.frame({"seq": 1, "topic": "life", "kind": "event", "ts": 0, "data": {}})
        recorder.close()
        wire = self.directory / recording.WIRE
        wire.write_text(wire.read_text() + '{"seq": 2, "topic": "li')

        back = recording.load(self.directory)
        self.assertEqual(len(back.frames), 1)

    def test_damage_anywhere_but_the_end_is_refused(self):
        recorder = recording.Recorder(self.directory, {})
        for index in range(3):
            recorder.frame({"seq": index, "topic": "life", "kind": "event", "ts": 0, "data": {}})
        recorder.close()
        wire = self.directory / recording.WIRE
        lines = wire.read_text().splitlines()
        lines[0] = '{"seq": 0, "top'
        wire.write_text("\n".join(lines) + "\n")

        with self.assertRaises(recording.Unreadable):
            recording.load(self.directory)

    def test_a_future_version_is_refused_rather_than_decoded(self):
        recorder = recording.Recorder(self.directory, {})
        recorder.close()
        meta = self.directory / recording.META
        meta.write_text(json.dumps({"v": recording.VERSION + 1}))
        with self.assertRaises(recording.Unreadable):
            recording.load(self.directory)

    def test_a_directory_that_is_not_a_recording_says_so(self):
        with self.assertRaises(recording.Unreadable):
            recording.load(self.directory)


class TeeTest(unittest.TestCase):
    """Where the tee sits decides what it can be missing."""

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="nova-tee-"))

    def tearDown(self):
        for child in self.directory.iterdir():
            child.unlink()
        self.directory.rmdir()

    def test_the_recording_holds_what_the_frame_window_dropped(self):
        """The window sheds console frames when a batch overruns, and a
        recording taken from what a client received would be missing
        exactly what the busiest moment produced."""
        recorder = recording.Recorder(self.directory, {})
        store = StateStore(Envelopes(Clock()), FrameWindow(max_frames=4), on_frame=recorder.frame)
        for index in range(20):
            store.publish(Topic.CONSOLE, Kind.EVENT, {"line": index})
        delivered = store.drain()
        recorder.close()

        back = recording.load(self.directory)
        self.assertLess(len(delivered), 20, "the window was expected to shed frames")
        self.assertEqual(len(back.frames), 20)

    def test_a_bridge_with_no_recorder_publishes_exactly_as_before(self):
        store = StateStore(Envelopes(Clock()))
        store.publish(Topic.LIFE, Kind.EVENT, {"phase": "x"})
        self.assertEqual(len(store.drain()), 1)


if __name__ == "__main__":
    unittest.main()
