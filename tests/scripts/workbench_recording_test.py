"""Writing a run down, and getting the same run back.

The claim a recording makes is that it holds what the wire held. These
hold it to that: same envelopes, same order, same records — and more
than a client saw, because the tee sits ahead of the window that drops
frames when a batch overruns.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from novakit.image import abi  # noqa: E402
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


class Recorded(unittest.TestCase):
    """A directory to record into, and a UI root to serve a bridge from.

    Every class below wants one or both, made and removed the same way.
    """

    def setUp(self):
        self.directory = self.tmpdir("run")
        self.ui = self.tmpdir("ui")

    def tmpdir(self, name: str) -> Path:
        made = Path(tempfile.mkdtemp(prefix=f"nova-{name}-"))
        self.addCleanup(shutil.rmtree, made, ignore_errors=True)
        return made


class RoundTripTest(Recorded):
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
        self.assertTrue(back.meta["complete"])

    def test_records_come_back_at_the_firmwares_own_width(self):
        recorder = recording.Recorder(self.directory)
        written = records(7)
        recorder.drained(written)
        recorder.close()

        back = recording.load(self.directory)
        self.assertEqual(back.records, written)

    def test_a_reader_sees_what_a_flush_has_not_reached_yet_as_absent(self):
        """Buffered on purpose: publish() is synchronous and on the
        bridge's only thread, so the disk write belongs to the loop that
        was already waking every 50 ms."""
        recorder = recording.Recorder(self.directory, {})
        recorder.frame({"seq": 1, "topic": "life", "kind": "event", "ts": 0, "data": {}})
        wire = recorder.directory / recording.WIRE
        self.assertEqual(wire.read_text(), "")
        recorder.flush()
        self.assertEqual(len(wire.read_text().splitlines()), 1)
        recorder.close()

    def test_a_run_killed_mid_line_still_loads(self):
        """An ordinary way for a recording to end. Refusing the whole
        file over its last half-written line would throw away the run
        that was worth recording."""
        recorder = recording.Recorder(self.directory, {})
        recorder.frame({"seq": 1, "topic": "life", "kind": "event", "ts": 0, "data": {}})
        recorder.close()
        wire = recorder.directory / recording.WIRE
        wire.write_text(wire.read_text() + '{"seq": 2, "topic": "li')

        back = recording.load(self.directory)
        self.assertEqual(len(back.frames), 1)

    def test_damage_anywhere_but_the_end_is_refused(self):
        recorder = recording.Recorder(self.directory, {})
        for index in range(3):
            recorder.frame({"seq": index, "topic": "life", "kind": "event", "ts": 0, "data": {}})
        recorder.close()
        wire = recorder.directory / recording.WIRE
        lines = wire.read_text().splitlines()
        lines[0] = '{"seq": 0, "top'
        wire.write_text("\n".join(lines) + "\n")

        with self.assertRaises(recording.Unreadable):
            recording.load(self.directory)

    def test_a_future_version_is_refused_rather_than_decoded(self):
        recorder = recording.Recorder(self.directory, {})
        recorder.close()
        meta = recorder.directory / recording.META
        meta.write_text(json.dumps({"v": recording.VERSION + 1}))
        with self.assertRaises(recording.Unreadable):
            recording.load(self.directory)

    def test_a_directory_that_is_not_a_recording_says_so(self):
        with self.assertRaises(recording.Unreadable):
            recording.load(self.directory)

    def test_a_restart_starts_a_new_recording(self):
        """Everything downstream reads a recording as one monotonic
        stream, and a restart begins the machine's clock again.
        Concatenated, two runs make a file whose span reads 1000 -> 59
        and whose windows answer with the wrong records — and which says
        nothing about it until somebody replays it.
        """
        recorder = recording.Recorder(self.directory, {})
        recorder.for_run(1)
        recorder.drained(records(10, first=1_000))
        recorder.for_run(2)  # the machine restarted
        recorder.drained(records(10, first=10))
        recorder.close()

        runs = sorted(child.name for child in self.directory.iterdir())
        self.assertEqual(runs, ["run-1", "run-2"])
        first = recording.load(self.directory / "run-1")
        second = recording.load(self.directory / "run-2")
        self.assertEqual(first.meta["run_id"], 1)
        self.assertEqual(second.meta["run_id"], 2)
        # Each is monotonic on its own, which is the property the
        # history's bisection needs and the concatenation destroyed.
        for run in (first, second):
            stamps = [record.ts for record in run.records]
            self.assertEqual(stamps, sorted(stamps))

    def test_the_first_launch_does_not_start_a_second_recording(self):
        """The frames before it — the topology, the build, the launch —
        are that run's opening, not a recording of their own."""
        recorder = recording.Recorder(self.directory, {})
        recorder.frame({"seq": 1, "topic": "topo", "kind": "snapshot", "ts": 0, "data": {}})
        recorder.for_run(1)
        recorder.for_run(1)  # every flush tick asks; only a change rolls
        recorder.close()
        self.assertEqual([child.name for child in self.directory.iterdir()], ["run-1"])
        self.assertEqual(len(recording.load(self.directory).frames), 1)

    def test_a_rolled_run_opens_with_the_world_it_is_a_recording_of(self):
        """A run's own description is published while the previous run is
        still the current one: a select builds and publishes the new
        topology, and only the launch that follows bumps the id this
        rolls on. On a two-demo session the second demo's world lands in
        the first demo's file and run-2 holds no topology at all, which
        replays as the empty pickable world with no guests.
        """
        recorder = recording.Recorder(self.directory, {"demo": "01_hello"})
        recorder.frame({"seq": 1, "topic": "topo", "kind": "snapshot", "ts": 0,
                        "data": {"demo": "01_hello", "guests": [{"name": "hello"}]}})
        recorder.for_run(1)
        recorder.frame({"seq": 2, "topic": "console", "kind": "event", "ts": 1, "data": {}})
        # The reader picks a second demo: prepared, published, and only
        # then launched.
        recorder.frame({"seq": 3, "topic": "topo", "kind": "snapshot", "ts": 2,
                        "data": {"demo": "02_timer", "guests": [{"name": "timer"}]}})
        recorder.for_run(2)
        recorder.close()

        second = recording.load(self.directory / "run-2")
        world = [frame for frame in second.frames if frame["topic"] == "topo"]
        self.assertEqual([frame["data"]["demo"] for frame in world], ["02_timer"])
        # Verbatim, not minted here: a fresh envelope would burn a
        # sequence number every live client reads as a hole.
        self.assertEqual(world[0]["seq"], 3)

    def test_a_run_is_named_by_the_world_it_recorded(self):
        """Not by the target the bridge was launched with. Those agree
        only until somebody picks a second demo, after which the launch
        names a run that ended — and both files claimed the first."""
        recorder = recording.Recorder(self.directory, {"demo": "01_hello"})
        recorder.for_run(1)
        recorder.frame({"seq": 1, "topic": "topo", "kind": "snapshot", "ts": 0,
                        "data": {"demo": "02_timer", "variant": "smp"}})
        recorder.for_run(2)
        recorder.close()

        self.assertEqual(recording.load(self.directory / "run-1").meta["demo"], "01_hello")
        second = recording.load(self.directory / "run-2").meta
        self.assertEqual((second["demo"], second["variant"]), ("02_timer", "smp"))

    def test_the_newest_run_is_what_a_root_loads(self):
        """`--record DIR` leaves a directory of runs, so the thing a
        reader has in hand is as often the root as a run."""
        recorder = recording.Recorder(self.directory, {})
        recorder.for_run(1)
        recorder.for_run(2)
        recorder.close()
        self.assertEqual(recording.load(self.directory).meta["run_id"], 2)

    def test_the_newest_run_is_the_one_the_writer_numbered_last(self):
        """By the number the recorder handed out, not by the filesystem's
        clock. A recording is a thing people copy to each other, and a
        copy re-stamps every mtime in whatever order the directory was
        walked in — which silently changed which run a replay showed.
        Nor by name: run-10 follows run-9.
        """
        recorder = recording.Recorder(self.directory, {})
        for run_id in range(1, 11):
            recorder.for_run(run_id)
        recorder.close()

        for age, child in enumerate(sorted(self.directory.iterdir())):
            os.utime(child / recording.META, (1_000 - age, 1_000 - age))
        self.assertEqual(recording.load(self.directory).meta["run_id"], 10)

    def test_a_directory_holding_a_recording_is_not_opened_for_writing(self):
        """It is somebody's evidence, and "w" would have taken it."""
        recording.Recorder(self.directory, {}).close()
        with self.assertRaises(FileExistsError):
            recording.Recorder(self.directory, {})


class KilledTest(Recorded):
    """A recording is readable from the moment it is opened.

    Deferring that to a clean exit made every file's readability
    conditional on how the process ended — and the run somebody most
    wants back is the one that ended badly.
    """

    def killed(self) -> None:
        """A recorder that got as far as a flush and no further."""
        recorder = recording.Recorder(self.directory, {"demo": "13_linux"})
        recorder.for_run(1)
        recorder.note(freq_hz=62_500_000)
        for index in range(3):
            recorder.frame({"seq": index, "topic": "console", "kind": "event",
                            "ts": index, "data": {"line": index}})
        recorder.drained(records(4))
        recorder.flush()
        # What a SIGKILL leaves behind: everything flushed, nothing
        # finished. The handles are released the way the kernel would
        # have released them — close(), which is what a clean exit
        # calls, is deliberately not called.
        recorder._wire.close()
        recorder._records.close()

    def test_a_run_that_was_killed_is_still_a_recording(self):
        self.killed()
        back = recording.load(self.directory)
        self.assertEqual(len(back.frames), 3)
        self.assertEqual(len(back.records), 4)

    def test_it_says_that_it_is_one(self):
        """The one fact the files cannot answer: a killed run looks
        exactly like a finished one, minus a line. A reader who is not
        told reads the end of the file as the end of the run."""
        self.killed()
        self.assertFalse(recording.load(self.directory).meta["complete"])
        recording.load(self.directory)  # and it is readable either way

    def test_the_clock_its_timestamps_are_in_survives_the_kill(self):
        """Learned from the region header partway through the run. Held
        until close, a killed recording is a pile of counter values with
        nothing to turn them back into a duration."""
        self.killed()
        self.assertEqual(recording.load(self.directory).meta["freq_hz"], 62_500_000)

    def test_a_killed_recording_is_not_opened_for_writing(self):
        """The guard exists to protect somebody's evidence, and this is
        the recording that cannot be reproduced. It read the meta to
        decide, so a run that never wrote one was invisible to it — and
        the next `--record` to the same path truncated the wire log to
        nothing."""
        self.killed()
        with self.assertRaises(FileExistsError):
            recording.Recorder(self.directory, {})
        self.assertGreater((self.directory / "run-1" / recording.WIRE).stat().st_size, 0)


class TeeTest(Recorded):
    """Where the tee sits decides what it can be missing."""

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

    def test_stamping_a_frame_tells_nobody(self):
        """Publishing means "tell every client"; stamping means "give me
        one frame with the next sequence", for a caller handing it to one
        socket itself. They were the same function, and the replay path
        wanted the second."""
        store = StateStore(Envelopes(Clock()))
        first = store.stamp(Topic.CONSOLE, Kind.EVENT, {"line": "a"})
        second = store.stamp(Topic.CONSOLE, Kind.EVENT, {"line": "b"})
        self.assertEqual(store.drain(), [])
        self.assertEqual(store.window.dropped, 0)
        # Still this connection's ordering, which is why the sequence
        # is minted rather than reused from the recording.
        self.assertLess(first["seq"], second["seq"])


class IdentityTest(Recorded):
    """A replay is answered by the live code.

    A separate path would be a second bridge, and from its first
    divergence there are two accounts of one run with nothing to settle
    which is right.
    """

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

    def test_opening_a_replay_does_not_report_the_bridge_falling_behind(self):
        """It did. Handing a client the whole run went through the
        broadcast window, which sheds frames when a batch overruns: 4905
        of 9000 shed, 4097 re-sent on the next flush, and the shedding
        published as `frames-dropped` — so the badge that means "the
        bridge could not keep up" lit on merely opening a recording.
        """
        from novakit.services.workbench.server import Bridge

        recorder = recording.Recorder(self.directory, {"freq_hz": 1})
        for index in range(9_000):
            recorder.frame({"seq": index, "topic": "console", "kind": "event",
                            "ts": index, "src": "serial", "data": {"line": index}})
        recorder.close()

        bridge = Bridge(ui_root=self.ui)
        bridge.load_replay(recording.load(self.directory))
        payload = bridge._connect_payload()

        self.assertEqual(len(payload), 9_001)  # the run, plus this connect's topo
        self.assertEqual(bridge.store.window.dropped, 0)
        again = bridge.store.drain()
        self.assertEqual([f["topic"] for f in again], ["topo"])
        self.assertEqual([f for f in again if f["data"].get("phase") == "frames-dropped"], [])

    def test_a_kind_or_src_this_build_never_heard_of_survives(self):
        """A recording carries what the run that made it wrote. Coercing
        an unfamiliar field into this build's enum raised, which killed
        the connection over a value the reader never looked at."""
        from novakit.services.workbench.server import Bridge

        recorder = recording.Recorder(self.directory, {"freq_hz": 1})
        recorder.frame({"seq": 1, "topic": "console", "kind": "gossip", "ts": 3,
                        "src": "martian", "data": {"line": "from a later build"}})
        recorder.close()

        bridge = Bridge(ui_root=self.ui)
        bridge.load_replay(recording.load(self.directory))
        odd = [f for f in bridge._connect_payload() if f["topic"] == "console"]
        self.assertEqual(len(odd), 1)
        self.assertEqual((odd[0]["kind"], odd[0]["src"]), ("gossip", "martian"))

    def test_a_replay_shows_the_world_the_run_ended_up_describing(self):
        """A run republishes its world when what it can witness changes:
        EL2 places the trace rings well after the topology first goes
        out, and an edge that was grey because nothing could watch it
        becomes direct the moment something can. Taking the first
        description threw every such upgrade away and drew the board as
        it looked before the run had proved anything."""
        from novakit.services.workbench.server import Bridge

        recorder = recording.Recorder(self.directory, {"freq_hz": 1})
        for seq, grade in enumerate(("none", "direct"), start=1):
            recorder.frame({"seq": seq, "topic": "topo", "kind": "snapshot", "ts": seq,
                            "data": {"board": {"edges": [{"id": "post", "grade": grade}]}}})
        recorder.close()

        bridge = Bridge(ui_root=self.ui)
        bridge.load_replay(recording.load(self.directory))
        self.assertEqual(
            bridge.store.topology["board"]["edges"], [{"id": "post", "grade": "direct"}]
        )

    def test_a_connect_topology_is_not_replayed_to_the_next_joiner(self):
        """It describes the session as it stood for the one client that
        caused it, and every connect after gets its own. Kept, a stale
        phase and run identity arrive *after* the fresh copy that
        replaced them — and each reconnect costs the backlog a real
        frame of history."""
        from novakit.services.workbench.server import Bridge

        recorder = recording.Recorder(self.directory, {"freq_hz": 1})
        recorder.close()
        bridge = Bridge(ui_root=self.ui)
        bridge.load_replay(recording.load(self.directory))

        for _ in range(3):
            payload = bridge._connect_payload()
        self.assertEqual([frame["topic"] for frame in payload].count("topo"), 1)

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
        that silently does nothing is worse than one that says why.

        Walked off the dispatch table rather than named one by one, so a
        handler added with a machine on its mind is covered the day it is
        written instead of the day somebody remembers this test.
        """
        from novakit.services.workbench import server

        recorder = recording.Recorder(self.directory, {"freq_hz": 1})
        recorder.close()
        bridge = server.Bridge(ui_root=self.ui)
        bridge.load_replay(recording.load(self.directory))

        driving = [
            handler
            for handler in server.HANDLERS
            if handler.needs in (server.Needs.MACHINE, server.Needs.RUNNING)
        ]
        self.assertTrue(driving)
        for handler in driving:
            with self.subTest(topic=handler.topic.value):
                bridge.store.drain()
                bridge._handle_uplink(json.dumps({"topic": handler.topic.value, "data": {}}))
                said = [
                    frame["data"].get("reason", "")
                    for frame in bridge.store.drain()
                    if frame["topic"] == "life"
                ]
                self.assertTrue(
                    any(text.startswith(f"{handler.topic.value}: ") for text in said), said
                )
                self.assertTrue(any("replay" in text for text in said), said)

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


# The region a board reserves is a board number; a fixture states its
# own, big enough for the one small ring below and nothing more.
REGION_SIZE = 0x10000
RING = abi.read_defines(
    abi.TRACE_RING,
    ["NOVA_TRACE_HEADER_SIZE", "NOVA_TRACE_HEAD_OFF", "NOVA_TRACE_RECORDS_OFF"],
)


class DrainJoinTest(Recorded):
    """The index a seek moves on is read off the frames a drain publishes.

    Both halves were only ever checked apart: everything below builds
    the summary by hand and the recorded fixture is a frozen file, so a
    drain that stopped stamping its span would leave all of them green —
    and every cursor move landing at the end of the run.
    """

    def region(self, count: int) -> bytes:
        """A formatted region with `count` records in its one ring.

        Written with the reader's own packer, so the fixture cannot
        drift from the layout it is read back through.
        """
        buffer = bytearray(REGION_SIZE)
        trace.format_region(buffer, 0, rings=1, capacity=64, freq_hz=62_500_000)
        ring = RING["NOVA_TRACE_HEADER_SIZE"]
        for index, record in enumerate(records(count)):
            at = ring + RING["NOVA_TRACE_RECORDS_OFF"] + index * trace.REC_SIZE
            trace.pack_into(buffer, at, record)
        head = ring + RING["NOVA_TRACE_HEAD_OFF"]
        buffer[head : head + 8] = count.to_bytes(8, "little")
        return bytes(buffer)

    def test_the_span_a_drain_publishes_is_what_the_seek_index_reads(self):
        from novakit.services.workbench.server import Bridge
        from novakit.services.workbench.session import Surfaces

        surfaces = Surfaces(self.directory)
        surfaces.shm_path.write_bytes(self.region(8))
        bridge = Bridge(ui_root=self.ui, surfaces=surfaces)
        # The region sits at the start of the RAM aperture, so the
        # fixture is the region rather than half a gigabyte of run-up.
        bridge._board = {
            "NOVA_BOARD_PHYS_RAM_BASE": 0,
            "NOVA_BOARD_TRACE_PA": 0,
            "NOVA_BOARD_TRACE_SIZE": REGION_SIZE,
        }
        self.addCleanup(bridge._drop_tracer)

        bridge._pump_trace()

        published = bridge.store.drain()
        summary = [frame for frame in published if frame["topic"] == "trace"]
        rec = recording.Recording(directory=self.directory, meta={}, frames=published)
        # One pair: the newest record that drain took in, against the
        # frame that reported it.
        self.assertEqual(rec.drains, ((records(8)[-1].ts, summary[-1]["ts"]),))


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
        rec = recording.Recording(directory=Path("."), meta={}, frames=self.stream(900))
        self.assertEqual(rec.at(0)["sched.cpu"]["data"]["values"], [{"current": 0}])
        self.assertEqual(rec.at(5_000)["sched.cpu"]["data"]["values"], [{"current": 500}])
        self.assertEqual(rec.at(1 << 40)["sched.cpu"]["data"]["values"], [{"current": 899}])

    def test_the_checkpoint_interval_cannot_change_an_answer(self):
        """The index accelerates the fold; it never replaces it. Checked
        at every interval including the degenerate one — a single
        checkpoint at the start, equivalent to having none.
        """
        frames = self.stream(900)
        at_every = {
            every: recording.Recording(
                directory=Path("."), meta={}, frames=frames,
                marks=recording.fold(frames, every=every),
            )
            for every in (1, 7, 256, len(frames) + 1)
        }
        self.assertGreater(len(at_every[7].marks), len(at_every[256].marks))
        self.assertEqual(len(at_every[len(frames) + 1].marks), 1)
        for ts in (0, 137, 4_321, 8_990, 1 << 40):
            answers = [rec.at(ts) for rec in at_every.values()]
            with self.subTest(ts=ts):
                self.assertTrue(all(answer == answers[0] for answer in answers))

    def test_an_index_cannot_be_held_without_being_derived(self):
        """The same argument as not writing keyframes into the file, one
        level in: a Recording that had to be handed its index would have
        a caller who could forget, and a stale index is invisible
        because it is what gets read."""
        rec = recording.Recording(directory=Path("."), meta={}, frames=self.stream(900))
        self.assertEqual(rec.marks, recording.fold(rec.frames))
        self.assertEqual(rec.topics, ("sched.cpu",))
        self.assertEqual(len(rec.drains), 900)

    def test_the_topology_is_not_folded_into_the_state(self):
        """It is the world, published once and answered from the store;
        replaying it as a panel value would put a phase in a table."""
        frames = [{"seq": 1, "topic": "topo", "kind": "snapshot", "ts": 0,
                   "src": "bridge", "data": {"stops": []}}]
        rec = recording.Recording(directory=Path("."), meta={}, frames=frames)
        self.assertNotIn("topo", rec.at(1 << 40))
        self.assertEqual(rec.topics, ())

    def test_a_record_is_placed_at_the_drain_that_took_it_in(self):
        """Two clocks. Records carry the machine's, frames the bridge's,
        and every drain summary carries both — so the pairs are already
        in the stream and nothing has to be interpolated."""
        frames = self.stream(10)
        rec = recording.Recording(directory=Path("."), meta={}, frames=frames)
        # Stamped 3500: the first drain whose span reached it is the one
        # at 4000, published at ts 41.
        self.assertEqual(rec.wire_ts(3_500), 41)
        self.assertEqual(rec.wire_ts(0), 1)
        # Past the end of the run: the last frame there is.
        self.assertEqual(rec.wire_ts(1 << 40), frames[-1]["ts"])


class CursorTest(Recorded):
    """One number moves the strip, the panels and the console."""

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

    A recorded run is a deterministic input to the layers that otherwise
    need QEMU to exercise: the window protocol, gap placement, the fold
    and the seek. Only the browser stays outside.

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
        """On a real run, not a constructed one: the same answer at
        every checkpoint however finely the stream was folded."""
        coarse = recording.Recording(
            directory=FIXTURE, meta=self.recorded.meta, frames=self.recorded.frames,
            marks=recording.fold(self.recorded.frames, every=len(self.recorded.frames) + 1),
        )
        for mark in self.recorded.marks:
            with self.subTest(at=mark.at):
                self.assertEqual(self.recorded.at(mark.ts), coarse.at(mark.ts))

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
