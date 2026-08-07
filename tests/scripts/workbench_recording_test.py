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


class IdentityTest(unittest.TestCase):
    """A replay is answered by the live code, or it is not evidence.

    This is the whole constraint of the replay design. A path of its own
    would be a second bridge, and from the first divergence the two are
    two accounts of one run with nothing to say which is right.
    """

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="nova-id-"))
        self.ui = Path(tempfile.mkdtemp(prefix="nova-ui-"))

    def tearDown(self):
        for root in (self.directory, self.ui):
            for child in root.iterdir():
                child.unlink()
            root.rmdir()

    def answer(self, bridge, request):
        bridge.store.drain()  # discard whatever setup published
        bridge._answer_window(request)
        for frame in bridge.store.drain():
            if frame["topic"] == "trace" and frame["kind"] == "snapshot":
                return frame["data"]
        return None

    def test_one_window_has_one_answer_live_or_replayed(self):
        from novakit.services.workbench.server import Bridge

        written = records(600, first=1_000)
        recorder = recording.Recorder(self.directory, {"freq_hz": 62_500_000})
        recorder.frame({"seq": 1, "topic": "life", "kind": "event", "ts": 5,
                        "src": "bridge", "data": {"phase": "running"}})
        recorder.drained(written)
        recorder.close()

        live = Bridge(ui_root=self.ui)
        live._history.freq_hz = 62_500_000
        live._history.append(written)

        replayed = Bridge(ui_root=self.ui)
        replayed.load_replay(recording.load(self.directory))

        for buckets in (16, 8192):
            with self.subTest(buckets=buckets):
                request = {"op": "window", "from": 1_000, "to": 1_500, "buckets": buckets}
                self.assertEqual(self.answer(live, request), self.answer(replayed, request))
        # Including the filtered form a path tour uses.
        request = {"op": "window", "from": 1_000, "to": 1_600, "buckets": 8192,
                   "events": ["trap"]}
        self.assertEqual(self.answer(live, request), self.answer(replayed, request))

    def test_a_replay_says_it_is_one(self):
        from novakit.services.workbench.server import Bridge
        from novakit.services.workbench.session import Phase

        recorder = recording.Recorder(self.directory, {"freq_hz": 1})
        recorder.close()
        bridge = Bridge(ui_root=self.ui)
        bridge.load_replay(recording.load(self.directory))
        self.assertIs(bridge.session.phase, Phase.REPLAY)
        self.assertEqual(bridge._live_state()["phase"], "replay")

    def test_a_recording_cannot_be_driven(self):
        """Refused with a reason, not accepted and ignored: a control
        that silently does nothing is worse than one that says why."""
        from novakit.services.workbench.server import Bridge

        recorder = recording.Recorder(self.directory, {"freq_hz": 1})
        recorder.close()
        bridge = Bridge(ui_root=self.ui)
        bridge.load_replay(recording.load(self.directory))

        bridge.store.drain()
        bridge._handle_uplink(json.dumps({"topic": "target", "data": {"demo": "09_guest_smp"}}))
        bridge._handle_uplink(json.dumps({"topic": "halt", "data": {"cmd": "stop"}}))
        said = [
            frame["data"].get("reason", "")
            for frame in bridge.store.drain()
            if frame["topic"] == "life"
        ]
        self.assertTrue(any("replay" in text for text in said), said)
        self.assertTrue(any("halt: session is replay" in text for text in said), said)

    def test_the_recorded_world_is_the_one_the_client_is_given(self):
        """Its catalogue, its board map, its limits — not this process's
        guesses about a machine that is not here. And exactly one
        topology, or the recorded run's phase would overwrite the
        replay's and the reader would be told it is live."""
        from novakit.services.workbench.server import Bridge

        recorder = recording.Recorder(self.directory, {"freq_hz": 1})
        recorder.frame({"seq": 900, "topic": "topo", "kind": "snapshot", "ts": 0,
                        "src": "bridge", "data": {"stops": [{"id": "trap"}], "phase": "running"}})
        recorder.frame({"seq": 901, "topic": "console", "kind": "event", "ts": 3,
                        "src": "serial", "data": {"line": "hello"}})
        recorder.frame({"seq": 902, "topic": "console", "kind": "event", "ts": 4,
                        "src": "serial", "data": {"line": "world"}})
        recorder.close()

        bridge = Bridge(ui_root=self.ui)
        bridge.load_replay(recording.load(self.directory))
        payload = bridge._connect_payload()
        topos = [frame for frame in payload if frame["topic"] == "topo"]
        self.assertTrue(topos)
        for frame in topos:
            self.assertEqual(frame["data"]["stops"], [{"id": "trap"}])
        # Exactly one carries a phase, and it is this connection's.
        phased = [frame["data"]["phase"] for frame in topos if "phase" in frame["data"]]
        self.assertEqual(phased, ["replay"])
        # The recorded moment, not this process's clock: a replay
        # wearing "now" puts yesterday on screen as if it were live.
        console = [frame for frame in payload if frame["topic"] == "console"]
        self.assertEqual([frame["ts"] for frame in console], [3, 4])
        # The sequence, though, belongs to this socket. Carried over,
        # the recorded numbers would read to the client as a rewind and
        # every frame after the first would be dropped as a duplicate.
        seqs = [frame["seq"] for frame in console]
        self.assertEqual(seqs, sorted(seqs))
        self.assertTrue(all(seq < 900 for seq in seqs), seqs)


class SeekTest(unittest.TestCase):
    """Going back to a moment, from an index the stream produced.

    The alternative was writing keyframes into the file: a summary that
    can disagree with what it summarises, with the disagreement
    invisible because the summary is what gets read. Folded at load
    there is one description of the run and the other is a function of
    it, so "stale" is not a state it has.
    """

    def stream(self, count: int) -> list[dict]:
        frames = []
        for index in range(count):
            frames.append({
                "seq": index, "topic": "sched.cpu", "kind": "snapshot", "ts": index * 10,
                "src": "S", "data": {"values": [{"current": index}]},
            })
            frames.append({
                "seq": index, "topic": "trace", "kind": "event", "ts": index * 10 + 1,
                "src": "T", "data": {"span": {"to": index * 1000}},
            })
        return frames

    def test_the_state_at_a_moment_is_the_stream_folded_to_it(self):
        frames = self.stream(900)
        rec = recording.Recording(directory=Path("."), meta={}, frames=frames,
                                  marks=recording.fold(frames))
        self.assertEqual(rec.at(0)["sched.cpu"]["data"]["values"], [{"current": 0}])
        self.assertEqual(rec.at(5_000)["sched.cpu"]["data"]["values"], [{"current": 500}])
        self.assertEqual(rec.at(1 << 40)["sched.cpu"]["data"]["values"], [{"current": 899}])

    def test_the_index_only_makes_the_same_answer_cheaper(self):
        """A checkpoint that changed an answer would be a cache, which
        is the thing this is not."""
        frames = self.stream(900)
        indexed = recording.Recording(directory=Path("."), meta={}, frames=frames,
                                      marks=recording.fold(frames))
        bare = recording.Recording(directory=Path("."), meta={}, frames=frames, marks=[])
        for ts in (0, 137, 4_321, 8_990, 1 << 40):
            with self.subTest(ts=ts):
                self.assertEqual(indexed.at(ts), bare.at(ts))
        self.assertGreater(len(indexed.marks), 1)

    def test_the_topology_is_not_folded_into_the_state(self):
        """It is the world, published once and answered from the store;
        replaying it as a panel value would put a phase in a table."""
        frames = [{"seq": 1, "topic": "topo", "kind": "snapshot", "ts": 0,
                   "src": "bridge", "data": {"stops": []}}]
        rec = recording.Recording(directory=Path("."), meta={}, frames=frames,
                                  marks=recording.fold(frames))
        self.assertNotIn("topo", rec.at(1 << 40))

    def test_a_record_is_placed_at_the_drain_that_took_it_in(self):
        """Two clocks. Records carry the machine's, frames the bridge's,
        and every drain summary carries both — so the pairs are already
        in the stream and nothing has to be interpolated."""
        frames = self.stream(10)
        rec = recording.Recording(directory=Path("."), meta={}, frames=frames, marks=[])
        # Stamped 3500: the first drain whose span reached it is the one
        # at 4000, published at ts 41.
        self.assertEqual(rec.wire_ts(3_500), 41)
        self.assertEqual(rec.wire_ts(0), 1)
        # Past the end of the run: the last frame there is.
        self.assertEqual(rec.wire_ts(1 << 40), frames[-1]["ts"])


class CursorTest(unittest.TestCase):
    """One number moves the strip, the panels and the console."""

    def setUp(self):
        self.ui = Path(tempfile.mkdtemp(prefix="nova-ui-"))
        self.directory = Path(tempfile.mkdtemp(prefix="nova-cur-"))

    def tearDown(self):
        for root in (self.ui, self.directory):
            for child in root.iterdir():
                child.unlink()
            root.rmdir()

    def recorded(self):
        recorder = recording.Recorder(self.directory, {"freq_hz": 1_000_000})
        for index in range(4):
            recorder.frame({"seq": index * 2, "topic": "sched.cpu", "kind": "snapshot",
                            "ts": index * 10, "src": "S",
                            "data": {"values": [{"current": index}]}})
            recorder.frame({"seq": index * 2 + 1, "topic": "trace", "kind": "event",
                            "ts": index * 10 + 1, "src": "T",
                            "data": {"span": {"to": index * 100}}})
        # A topic the run only reads once, late: the S poller publishes
        # on change, so a value first seen halfway through is ordinary.
        recorder.frame({"seq": 99, "topic": "dev.dma", "kind": "snapshot", "ts": 35,
                        "src": "S", "data": {"values": {"count_": 1}}})
        recorder.close()
        return recording.load(self.directory)

    def bridge(self):
        from novakit.services.workbench.server import Bridge

        bridge = Bridge(ui_root=self.ui)
        bridge.load_replay(self.recorded())
        return bridge

    def test_a_seek_answers_with_the_panels_of_that_moment(self):
        bridge = self.bridge()
        bridge.store.drain()
        bridge._handle_uplink(json.dumps({"topic": "cursor", "data": {"ts": 150}}))
        frames = bridge.store.drain()

        panels = [f for f in frames if f["topic"] == "sched.cpu"]
        self.assertEqual(len(panels), 1)
        # ts 150 falls in the drain whose span reached 200, published at
        # wire 21 — by then the reading had advanced to 2.
        self.assertEqual(panels[0]["data"]["values"], [{"current": 2}])
        cursor = [f for f in frames if f["topic"] == "cursor"]
        self.assertEqual(cursor[0]["data"], {"ts": 150, "wire": 21, "unread": ["dev.dma"]})

    def test_a_moment_before_a_topic_was_read_says_so(self):
        """Silence about it would leave the topic's later reading on
        screen — a value the machine had not produced at the moment the
        reader asked to be returned to."""
        bridge = self.bridge()
        bridge.store.drain()
        bridge._handle_uplink(json.dumps({"topic": "cursor", "data": {"ts": 0}}))
        cursor = [f for f in bridge.store.drain() if f["topic"] == "cursor"]
        self.assertEqual(cursor[0]["data"]["unread"], ["dev.dma"])

        # And once the run has read it, it stops being unread.
        bridge.store.drain()
        bridge._handle_uplink(json.dumps({"topic": "cursor", "data": {"ts": 1 << 40}}))
        cursor = [f for f in bridge.store.drain() if f["topic"] == "cursor"]
        self.assertEqual(cursor[0]["data"]["unread"], [])

    def test_the_panels_arrive_as_ordinary_snapshots(self):
        """A seek that sent a payload of its own would teach the client
        a second way to take a value, and the two would come to
        disagree about which is a reading."""
        bridge = self.bridge()
        bridge.store.drain()
        bridge._handle_uplink(json.dumps({"topic": "cursor", "data": {"ts": 0}}))
        for frame in bridge.store.drain():
            if frame["topic"] == "sched.cpu":
                self.assertEqual(frame["kind"], "snapshot")
                self.assertIn("values", frame["data"])
                self.assertEqual(frame["src"], "S")

    def test_a_live_bridge_has_only_now(self):
        """A panel returned to an earlier reading on a running machine
        would be showing a value nothing can be checked against."""
        from novakit.services.workbench.server import Bridge

        bridge = Bridge(ui_root=self.ui)
        bridge.store.drain()
        bridge._handle_uplink(json.dumps({"topic": "cursor", "data": {"ts": 5}}))
        said = [f["data"].get("reason", "") for f in bridge.store.drain()]
        self.assertTrue(any("only a replay" in text for text in said), said)


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "workbench-run"


class FixtureTest(unittest.TestCase):
    """The bridge, against a real run that cannot change.

    Everything below this line was previously reachable only by
    launching QEMU, and so was checked by hand or not at all. A recorded
    run is a deterministic input to the layer that used to have none:
    the window protocol, the gap placement, the fold, the seek. What
    stays outside is the browser, which needs one.

    Recorded from demo 10 (two guests, console multiplexing) because it
    exercises more of the wire than a single-guest run: two console
    tabs, both vCPU slots, and a trace stream busy enough to have shape.
    """

    @classmethod
    def setUpClass(cls):
        cls.recorded = recording.load(FIXTURE)

    def test_the_fixture_is_a_run_and_not_a_stub(self):
        self.assertEqual(self.recorded.meta["demo"], "10_console_mux")
        self.assertGreater(len(self.recorded.frames), 50)
        self.assertGreater(len(self.recorded.records), 100)
        self.assertGreater(self.recorded.meta["freq_hz"], 0)

    def test_the_history_answers_windows_over_it(self):
        from novakit.services.workbench.history import History

        held = History(1 << 16)
        held.freq_hz = self.recorded.meta["freq_hz"]
        held.append(self.recorded.records)
        span = held.span()
        self.assertEqual(span.count, len(self.recorded.records))
        # A window over the middle holds fewer records than the whole,
        # and every one of them is inside it.
        middle = (span.first + span.last) // 2
        inside = held.window(span.first, middle)
        self.assertLess(len(inside), span.count)
        self.assertTrue(all(span.first <= r.ts <= middle for r in inside))

    def test_the_fold_is_a_function_of_the_stream(self):
        bare = recording.Recording(
            directory=FIXTURE, meta=self.recorded.meta, frames=self.recorded.frames
        )
        marks = self.recorded.marks or [recording.Checkpoint(0, 0, {})]
        for mark in marks:
            with self.subTest(at=mark.at):
                self.assertEqual(self.recorded.at(mark.ts), bare.at(mark.ts))

    def test_a_cursor_late_in_the_run_reads_more_than_one_early(self):
        first, last = self.recorded.frames[0]["ts"], self.recorded.frames[-1]["ts"]
        self.assertLess(len(self.recorded.at(first)), len(self.recorded.at(last)))

    def test_every_record_the_run_holds_decodes_or_is_a_gap(self):
        """A record nothing can name reaches the strip as a bare number
        and is skipped without a word."""
        from novakit.services.workbench import events

        for record in self.recorded.records:
            with self.subTest(code=record.code):
                self.assertIn(record.code, events.BY_CODE)


if __name__ == "__main__":
    unittest.main()
