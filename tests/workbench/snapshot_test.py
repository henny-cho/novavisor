"""The S layer: poll gating, change reporting, and RAM decoding."""

from __future__ import annotations

import importlib.util
import pickle
import struct
import tempfile
import unittest
from pathlib import Path

from novakit.image import abi, elfsym
from novakit.services.workbench import hardware, snapshot
from novakit.services.workbench.observations import (
    OBSERVATIONS,
    PUBLISH_HZ,
    Obs,
)

from tests.support import image as shared_image

ELF = shared_image.ELF
RAM_BASE = hardware.platform()["NOVA_BOARD_PHYS_RAM_BASE"]
# The reader's own parse of the published format, so the fixture below
# writes what the reader reads rather than a second description of it.
# Where payloads go is the publisher's half of that format, which the
# reader learns per slot and so does not parse.
_TLM = snapshot._TLM  # noqa: SLF001
_PLACE = abi.read_defines(
    abi.TELEMETRY,
    ["NOVA_TLM_PAYLOAD_OFF", "NOVA_TLM_PAYLOAD_BYTES", "NOVA_TLM_ALIGN"],
)


def _observed_top() -> int:
    """Highest physical address any observation reaches."""
    return max(
        obs.pa + snapshot.PAGE_LAYOUTS[obs.layout].size
        for obs in OBSERVATIONS
        if obs.pa is not None
    )


class FakeProvider:
    """A publisher whose answers the test dictates.

    `seq` and `stamp` count up per read so the poller's cursor is a
    moving thing rather than a constant, which is what makes "did this
    reader keep its own" a question with an answer.
    """

    def __init__(self):
        self.values: dict[str, object] = {}
        self.torn: set[str] = set()
        self.unmoved: set[str] = set()
        self.reads: list[str] = []
        self.since: list[int | None] = []
        self.ticket = 0

    def read(self, obs: Obs, *, live: bool = True, since: int | None = None):
        del live
        self.reads.append(obs.topic)
        self.since.append(since)
        if obs.topic in self.torn:
            raise elfsym.TornRead(obs.topic)
        if obs.topic in self.unmoved:
            raise snapshot.Unchanged(obs.topic)
        self.ticket += 1
        return snapshot.Reading(self.values[obs.topic], seq=self.ticket, stamp=100 + self.ticket)

    def close(self) -> None:
        pass


class _StamplessProvider:
    """A reader with no publisher behind it: values, no clock."""

    def read(self, obs: Obs, *, live: bool = True, since: int | None = None):
        del live, since
        return snapshot.Reading({"n": obs.topic})

    def close(self) -> None:
        pass


class PollerTest(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.provider = FakeProvider()
        self.observations = (
            Obs("fast", "nova::fast", rate_hz=20),
            Obs("slow", "nova::slow", rate_hz=2),
        )
        self.provider.values = {"fast": 1, "slow": 1}
        self.poller = snapshot.SnapshotPoller(
            self.provider, self.observations, monotonic=lambda: self.now
        )

    def test_rates_gate_reads_and_only_changes_report(self):
        first = self.poller.tick()
        self.assertEqual([obs.topic for obs, _ in first], ["fast", "slow"])

        self.now += 0.05  # only the 20 Hz observation is due; value unchanged
        self.assertEqual(self.poller.tick(), [])
        self.assertEqual(self.provider.reads.count("slow"), 1)

        self.provider.values["fast"] = 2
        self.now += 0.05
        changed = self.poller.tick()
        self.assertEqual([(obs.topic, value) for obs, value in changed], [("fast", 2)])

    def test_an_unmoved_value_costs_a_publish_and_nothing_else(self):
        # The publisher answering "it has not moved" is not the same as
        # reading it and finding it equal: nothing was decoded. What the
        # poller must not do is treat the silence as a value -- the
        # cached reading has to survive it, so the next real change is
        # still measured against what the UI is showing.
        first = self.poller.tick()
        self.assertEqual([obs.topic for obs, _ in first], ["fast", "slow"])

        self.provider.unmoved.add("fast")
        self.now += 0.05
        self.assertEqual(self.poller.tick(), [])

        # Still gated, and the cache was not clobbered: re-reading the
        # value it already published is not a change.
        self.provider.unmoved.clear()
        self.now += 0.05
        self.assertEqual(self.poller.tick(), [])

        self.provider.values["fast"] = 2
        self.now += 0.05
        self.assertEqual([(obs.topic, value) for obs, value in self.poller.tick()], [("fast", 2)])

    def test_torn_reads_are_retried_not_published(self):
        self.provider.torn.add("fast")
        self.assertEqual([obs.topic for obs, _ in self.poller.tick()], ["slow"])

        self.provider.torn.clear()
        self.now += 0.05
        recovered = self.poller.tick()
        self.assertEqual([obs.topic for obs, _ in recovered], ["fast"])

    def test_the_poller_brings_its_own_cursor(self):
        """The decode saving survives the cursor moving out of the
        provider: the poller asks "since the sequence I took", which is
        the question the provider used to answer for whoever read last.
        """
        self.poller.tick()
        taken = dict(zip(self.provider.reads, self.provider.since))
        self.assertEqual(taken, {"fast": None, "slow": None})  # nothing seen yet

        self.now += 0.05
        self.poller.tick()
        self.assertEqual(self.provider.since[-1], 1)  # the sequence it took

    def test_the_stamp_published_belongs_to_the_value_published(self):
        self.poller.tick()
        self.assertEqual(self.poller.stamp("fast"), 101)
        self.assertEqual(self.poller.stamp("slow"), 102)

        # Another reader takes a look. It moves the publisher's ticket,
        # and must move nothing this poller is holding.
        self.provider.read(self.observations[0])
        self.assertEqual(self.poller.stamp("fast"), 101)

    def test_a_topic_with_no_publisher_behind_it_has_no_stamp(self):
        """A scripted or replayed reading is placed by its arrival, and
        a made-up clock would be indistinguishable from a real one."""
        poller = snapshot.SnapshotPoller(
            _StamplessProvider(), self.observations, monotonic=lambda: self.now
        )
        poller.tick()
        self.assertIsNone(poller.stamp("fast"))


class SweepTest(unittest.TestCase):
    """A stopped machine can be read exhaustively; a running one cannot.

    The change gate answers "what moved", which is the right question
    while time passes and the wrong one at a breakpoint, where the
    reader wants the whole machine at that instant.
    """

    def setUp(self):
        self.now = 0.0
        self.provider = FakeProvider()
        self.observations = (
            Obs("fast", "nova::fast", rate_hz=20),
            Obs("slow", "nova::slow", rate_hz=2),
        )
        self.provider.values = {"fast": 1, "slow": 1}
        self.poller = snapshot.SnapshotPoller(
            self.provider, self.observations, monotonic=lambda: self.now
        )

    def test_sweep_reports_everything_including_the_unchanged(self):
        self.poller.tick()  # prime the cache with both values
        swept = self.poller.sweep()
        self.assertEqual([obs.topic for obs, _ in swept], ["fast", "slow"])

    def test_sweep_ignores_the_rate_gate(self):
        """The slow topic is not due for half a second; at a stop that
        is irrelevant, because nothing will change in the meantime."""
        self.poller.tick()
        self.now += 0.01
        self.assertEqual(self.poller.tick(), [])
        self.assertEqual(len(self.poller.sweep()), 2)

    def test_a_sweep_does_not_replay_as_changes_afterwards(self):
        """Resuming must not re-send what the stop already published."""
        self.provider.values = {"fast": 9, "slow": 9}
        self.poller.sweep()
        self.now += 10.0
        self.assertEqual(self.poller.tick(), [])

    def test_a_torn_value_is_skipped_not_faked(self):
        self.provider.torn.add("slow")
        swept = self.poller.sweep()
        self.assertEqual([obs.topic for obs, _ in swept], ["fast"])


@unittest.skipUnless(ELF.is_file(), "debug ELF not built")
@unittest.skipUnless(importlib.util.find_spec("elftools"), "pyelftools is not installed")
class ElfRamProviderTest(unittest.TestCase):
    """End-to-end address arithmetic against a synthetic RAM file."""

    def test_reads_a_seeded_scheduler_state(self):
        sched = shared_image.view().resolved["sched.cpu"]

        with tempfile.TemporaryDirectory() as directory:
            ram_path = Path(directory) / "guest-ram"
            with ram_path.open("wb") as ram:
                # Sparse; must span the PA-declared IVC page, which the
                # provider's size check covers too. Both ends come from
                # the manifest, so neither is a number typed in here.
                ram.truncate(_observed_top() - RAM_BASE)
                ram.seek(sched.address - RAM_BASE)
                # CpuSched: current=1, fp=kNoOwner, fp_trap=1, idling=0
                ram.write(struct.pack("<QQ??6x", 1, (1 << 64) - 1, True, False))

            # The one place the provider is left to resolve the image
            # itself, which is what `view=None` means. Everything else
            # here is handed the suite's parse, since paying seconds of
            # DWARF walk to reach an assertion about mmap arithmetic
            # buys nothing.
            provider = snapshot.ElfRamProvider(ELF, ram_path, RAM_BASE)
            self.addCleanup(provider.close)
            observed = {obs.topic: obs for obs in OBSERVATIONS}
            cpus = provider.read(observed["sched.cpu"]).value

        # kNoOwner reaches the wire as null, not as a number a JSON
        # reader would have to recognise.
        self.assertEqual(
            cpus[0],
            {"current": 1, "fp": None, "fp_trap": True, "idling": False},
        )
        self.assertEqual(
            cpus[1],
            {"current": 0, "fp": 0, "fp_trap": False, "idling": False},
        )

    def test_a_short_backend_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            ram_path = Path(directory) / "guest-ram"
            ram_path.write_bytes(b"\0" * 4096)
            with self.assertRaises(ValueError):
                snapshot.ElfRamProvider(ELF, ram_path, RAM_BASE, shared_image.view())

    def test_raw_memory_comes_back_at_the_address_asked_for(self):
        """What a page table walk needs: bytes at an address it learned
        by reading, with no symbol and no declared layout in sight."""
        marker = b"\xde\xad\xbe\xef" * 4
        at = RAM_BASE + 0x10_0000
        with tempfile.TemporaryDirectory() as directory:
            ram_path = Path(directory) / "guest-ram"
            with ram_path.open("wb") as ram:
                ram.truncate(_observed_top() - RAM_BASE)
                ram.seek(at - RAM_BASE)
                ram.write(marker)

            provider = snapshot.ElfRamProvider(ELF, ram_path, RAM_BASE, shared_image.view())
            self.addCleanup(provider.close)
            self.assertEqual(provider.read_bytes(at, len(marker)), marker)
            self.assertEqual(provider.read_bytes(at + 4, 4), marker[4:8])

    def test_a_read_running_off_the_backend_fails_instead_of_shortening(self):
        """An mmap slice past the end returns what there is. A table read
        that way decodes as invalid entries, and the walk would then call
        an address unmapped when it is only unreadable."""
        with tempfile.TemporaryDirectory() as directory:
            ram_path = Path(directory) / "guest-ram"
            with ram_path.open("wb") as ram:
                ram.truncate(_observed_top() - RAM_BASE)

            provider = snapshot.ElfRamProvider(ELF, ram_path, RAM_BASE, shared_image.view())
            self.addCleanup(provider.close)
            top = RAM_BASE + (_observed_top() - RAM_BASE)
            with self.assertRaises(ValueError):
                provider.read_bytes(top - 8, 4096)
            with self.assertRaises(ValueError):
                provider.read_bytes(RAM_BASE - 4096, 4096)


class Publisher:
    """As much of the firmware's publisher as a reader can tell apart.

    A header the reader checks, one descriptor per observed global, and
    payloads behind them. Turns are taken by hand, so a test can put one
    read on each side of one — which is the only way to ask whether two
    readers of one region interfere.

    The layout comes from the same header the reader parses, so this
    fixture cannot drift from the format it is standing in for.
    """

    def __init__(self, view, ram_path: Path):
        self._path = ram_path
        self.base = view.symbols.extent_of(snapshot.TELEMETRY_REGION)[0]
        self._region = bytearray(_PLACE["NOVA_TLM_PAYLOAD_OFF"] + _PLACE["NOVA_TLM_PAYLOAD_BYTES"])
        self._slot: dict[str, int] = {}
        self._at: dict[str, int] = {}

        topics = [obs for obs in OBSERVATIONS if obs.pa is None]
        at = _PLACE["NOVA_TLM_PAYLOAD_OFF"]
        align = _PLACE["NOVA_TLM_ALIGN"]
        for index, obs in enumerate(topics):
            size = view.resolved[obs.topic].size
            self._slot[obs.topic], self._at[obs.topic] = index, at
            self._descriptor(
                index,
                source=view.addresses[obs.symbol],
                seq=2,
                stamp=1000,
                at=at,
                size=size,
            )
            at += -(-size // align) * align

        header = _TLM["NOVA_TLM_HEADER_SIZE"]
        self._put(_TLM["NOVA_TLM_MAGIC_OFF"], 8, _TLM["NOVA_TLM_MAGIC"])
        self._put(_TLM["NOVA_TLM_VERSION_OFF"], 4, _TLM["NOVA_TLM_VERSION"])
        self._put(_TLM["NOVA_TLM_SLOTS_OFF"], 4, len(topics))
        self._put(_TLM["NOVA_TLM_DESCSIZE_OFF"], 4, _TLM["NOVA_TLM_DESC_SIZE"])
        self._put(_TLM["NOVA_TLM_PERIOD_OFF"], 4, round(1_000_000 / PUBLISH_HZ))
        self._put(_TLM["NOVA_TLM_BUDGET_OFF"], 4, at - header)
        self._put(_TLM["NOVA_TLM_BYTES_OFF"], 4, at - header)
        self.flush()

    def _put(self, offset: int, width: int, value: int) -> None:
        self._region[offset : offset + width] = value.to_bytes(width, "little")

    def _descriptor(self, index: int, *, source: int, seq: int, stamp: int, at: int, size: int):
        base = _TLM["NOVA_TLM_HEADER_SIZE"] + index * _TLM["NOVA_TLM_DESC_SIZE"]
        self._put(base + _TLM["NOVA_TLM_DESC_SOURCE_OFF"], 8, source)
        self._put(base + _TLM["NOVA_TLM_DESC_SEQ_OFF"], 8, seq)
        self._put(base + _TLM["NOVA_TLM_DESC_STAMP_OFF"], 8, stamp)
        self._put(base + _TLM["NOVA_TLM_DESC_AT_OFF"], 4, at)
        self._put(base + _TLM["NOVA_TLM_DESC_BYTES_OFF"], 4, size)

    def _field(self, topic: str, offset: int) -> int:
        """Where one descriptor field of one slot sits in the region."""
        base = _TLM["NOVA_TLM_HEADER_SIZE"] + self._slot[topic] * _TLM["NOVA_TLM_DESC_SIZE"]
        return base + offset

    def _seq(self, topic: str) -> int:
        at = self._field(topic, _TLM["NOVA_TLM_DESC_SEQ_OFF"])
        return int.from_bytes(self._region[at : at + 8], "little")

    def turn(self, topic: str, payload: bytes, *, stamp: int) -> None:
        """One publish turn for one slot: new bytes, new clock, sequence on."""
        at = self._at[topic]
        self._region[at : at + len(payload)] = payload
        self._put(self._field(topic, _TLM["NOVA_TLM_DESC_SEQ_OFF"]), 8, self._seq(topic) + 2)
        self._put(self._field(topic, _TLM["NOVA_TLM_DESC_STAMP_OFF"]), 8, stamp)
        self.flush()

    def open_window(self, topic: str) -> None:
        """Stop inside the copy: an odd sequence is a writer at work."""
        self._put(self._field(topic, _TLM["NOVA_TLM_DESC_SEQ_OFF"]), 8, self._seq(topic) + 1)
        self.flush()

    def flush(self) -> None:
        with self._path.open("r+b") as ram:
            ram.seek(self.base - RAM_BASE)
            ram.write(self._region)


@unittest.skipUnless(ELF.is_file(), "debug ELF not built")
@unittest.skipUnless(importlib.util.find_spec("elftools"), "pyelftools is not installed")
class TelemetryProviderTest(unittest.TestCase):
    """A published region has more than one reader, and must.

    The poller draws the panels, a walk asks where a guest is rooted,
    a scenario's predicate checks a field. What each of them last saw,
    and when the copy it saw was made, is a fact about that reader —
    so neither can be kept beside the bytes without one reader
    answering for another.
    """

    def setUp(self):
        self.view = shared_image.view()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        ram_path = Path(directory.name) / "guest-ram"
        with ram_path.open("wb") as ram:
            ram.truncate(_observed_top() - RAM_BASE)  # sparse
        self.publisher = Publisher(self.view, ram_path)
        self.provider = snapshot.TelemetryProvider(
            snapshot.ElfRamProvider(ELF, ram_path, RAM_BASE, self.view), self.view
        )
        self.addCleanup(self.provider.close)
        self.obs = next(obs for obs in OBSERVATIONS if obs.topic == "sched.cpu")

    @staticmethod
    def _cpu(current: int) -> bytes:
        # CpuSched: current, fp=kNoOwner, fp_trap, idling
        return struct.pack("<QQ??6x", current, (1 << 64) - 1, True, False)

    def test_a_reader_without_a_cursor_always_gets_the_value(self):
        """The measured failure: a second reader asking about a topic
        used to be told "unchanged" on the strength of the first one's
        copy, which left the first one's panel frozen and nothing to
        connect the two."""
        first = self.provider.read(self.obs)
        second = self.provider.read(self.obs)
        self.assertEqual(first.value, second.value)
        self.assertEqual(first.seq, second.seq)

    def test_the_saving_is_the_cursor_the_caller_brings(self):
        taken = self.provider.read(self.obs)
        with self.assertRaises(snapshot.Unchanged):
            self.provider.read(self.obs, since=taken.seq)

        self.publisher.turn("sched.cpu", self._cpu(1), stamp=2000)
        moved = self.provider.read(self.obs, since=taken.seq)
        self.assertEqual(moved.value[0]["current"], 1)

    def test_one_reader_cannot_move_another_reader_s_clock(self):
        """The stamp is what the shadow age and the timeline placement
        are measured from. Kept on the provider, a read landing across a
        publish turn dated the other reader's value one turn late."""
        poller = snapshot.SnapshotPoller(self.provider, (self.obs,))
        poller.tick()
        self.assertEqual(poller.stamp("sched.cpu"), 1000)

        self.publisher.turn("sched.cpu", self._cpu(1), stamp=2000)
        walked = self.provider.read(self.obs)
        self.assertEqual(walked.stamp, 2000)
        self.assertEqual(poller.stamp("sched.cpu"), 1000, "the poller still holds its own copy")

    def test_a_poller_reading_after_a_turn_takes_both_halves(self):
        now = [0.0]
        poller = snapshot.SnapshotPoller(self.provider, (self.obs,), monotonic=lambda: now[0])
        poller.tick()
        self.publisher.turn("sched.cpu", self._cpu(1), stamp=2000)
        now[0] += 1.0
        changed = poller.tick()
        self.assertEqual([value[0]["current"] for _obs, value in changed], [1])
        self.assertEqual(poller.stamp("sched.cpu"), 2000)

    def test_a_read_inside_the_publisher_s_window_is_torn(self):
        self.publisher.open_window("sched.cpu")
        with self.assertRaises(elfsym.TornRead):
            self.provider.read(self.obs)


class ChangedMaskTest(unittest.TestCase):
    """What a stop is for is seeing what moved.

    A stop publishes the whole machine — twenty-eight topics — and
    between two consecutive binds three or four values actually changed.
    Finding those by eye across every panel is the work this removes.
    """

    def test_only_the_leaf_that_moved_is_marked(self):
        before = {"cpu": [{"current": 1, "fp": None}, {"current": 0, "fp": 0}]}
        after = {"cpu": [{"current": 1, "fp": None}, {"current": 2, "fp": 0}]}
        # "the scheduler changed" is true of almost every stop and says
        # nothing; the index and the field are the answer.
        self.assertEqual(snapshot.changed_mask(before, after), {"cpu": {"1": {"current": True}}})

    def test_the_mask_is_shaped_like_the_value(self):
        """The whole point. A renderer walking the reading walks the
        mask by the same indexing, so there is no path grammar for the
        two sides to disagree about — and list indices are string keys
        so an array and an object are one walk on the far side.
        """
        before = {"rows": [{"a": 1}, {"a": 2}], "n": 1}
        after = {"rows": [{"a": 1}, {"a": 9}], "n": 1}
        mask = snapshot.changed_mask(before, after)
        self.assertIs(mask["rows"]["1"]["a"], True)
        self.assertNotIn("0", mask["rows"])  # sparse: only what moved
        self.assertNotIn("n", mask)

    def test_identical_readings_mark_nothing(self):
        reading = {"a": 1, "b": {"c": [1, 2, 3]}}
        self.assertEqual(snapshot.changed_mask(reading, dict(reading)), {})

    def test_a_field_that_appeared_or_left_is_a_change(self):
        """A reading that grew or lost a field is exactly the kind of
        thing worth being told about, not a comparison to skip."""
        self.assertEqual(snapshot.changed_mask({"a": 1}, {"a": 1, "b": 2}), {"b": True})
        self.assertEqual(snapshot.changed_mask({"a": [1]}, {"a": [1, 2]}), {"a": {"1": True}})

    def test_a_reading_that_changed_shape_is_a_change_at_that_node(self):
        """A list where a dict was is not a comparison to descend into;
        it is the change."""
        self.assertIs(snapshot.changed_mask({"a": [1]}, {"a": {"0": 1}})["a"], True)

    def test_a_scalar_topic_marks_itself(self):
        self.assertIs(snapshot.changed_mask(3, 4), True)
        self.assertIs(snapshot.changed_mask(3, 3), False)

    def test_the_badge_count_is_the_number_of_leaves(self):
        """One number the tab shows and the cells must add up to. Counted
        here because the mask's shape belongs to this module."""
        before = {"cpu": [{"a": 1, "b": 1}, {"a": 1, "b": 1}]}
        after = {"cpu": [{"a": 2, "b": 1}, {"a": 1, "b": 3}]}
        self.assertEqual(snapshot.moved_count(snapshot.changed_mask(before, after)), 2)
        self.assertEqual(snapshot.moved_count({}), 0)
        self.assertEqual(snapshot.moved_count(True), 1)
        self.assertEqual(snapshot.moved_count(False), 0)

    def test_a_type_change_is_a_change_not_a_descent(self):
        self.assertIs(snapshot.changed_mask({"a": 1}, [1]), True)


@unittest.skipUnless(ELF.is_file(), "debug ELF not built")
@unittest.skipUnless(importlib.util.find_spec("elftools"), "pyelftools is not installed")
class ImageViewTest(unittest.TestCase):
    """Reading the image is separable from using it.

    The point of the split is that the reading — three seconds of pure
    Python, landing while the guest boots and the trace rings burst —
    can happen in another process. That only holds if what comes back
    is data, so the test is that it survives the trip.
    """

    def test_a_resolved_image_survives_being_sent_between_processes(self):
        view = shared_image.view()
        restored = pickle.loads(pickle.dumps(view))

        self.assertEqual(restored.resolved.keys(), view.resolved.keys())
        for topic, symbol in view.resolved.items():
            self.assertEqual(restored.resolved[topic], symbol)
        self.assertTrue(restored.symbols.has("nova::trace::g_ring"))

    def test_a_provider_given_a_view_never_opens_the_image(self):
        """Which is what lets the parse happen somewhere else: the
        provider that maps RAM is not the thing that read the ELF."""
        view = shared_image.view()
        with tempfile.TemporaryDirectory() as directory:
            ram_path = Path(directory) / "guest-ram"
            with ram_path.open("wb") as ram:
                ram.truncate(_observed_top() - RAM_BASE)  # sparse
            provider = snapshot.ElfRamProvider(Path("/nonexistent.elf"), ram_path, RAM_BASE, view)
            self.addCleanup(provider.close)
            self.assertTrue(provider.symbols.has("nova::trace::g_ring"))
