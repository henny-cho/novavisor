"""The S layer: poll gating, change reporting, and RAM decoding."""

from __future__ import annotations

import importlib.util
import pickle
import struct
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import workbench_image  # noqa: E402
from novakit.services.workbench import elfsym, hardware, snapshot  # noqa: E402
from novakit.services.workbench.observations import OBSERVATIONS, Obs  # noqa: E402

ELF = workbench_image.ELF
RAM_BASE = hardware.platform()["NOVA_BOARD_PHYS_RAM_BASE"]


def _observed_top() -> int:
    """Highest physical address any observation reaches."""
    return max(
        obs.pa + snapshot.PAGE_LAYOUTS[obs.layout].size
        for obs in OBSERVATIONS
        if obs.pa is not None
    )


class FakeProvider:
    def __init__(self):
        self.values: dict[str, object] = {}
        self.torn: set[str] = set()
        self.reads: list[str] = []

    def read(self, obs: Obs) -> object:
        self.reads.append(obs.topic)
        if obs.topic in self.torn:
            raise elfsym.TornRead(obs.topic)
        return self.values[obs.topic]

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

    def test_torn_reads_are_retried_not_published(self):
        self.provider.torn.add("fast")
        self.assertEqual([obs.topic for obs, _ in self.poller.tick()], ["slow"])

        self.provider.torn.clear()
        self.now += 0.05
        recovered = self.poller.tick()
        self.assertEqual([obs.topic for obs, _ in recovered], ["fast"])


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
        sched = workbench_image.index().resolve("nova::vcpu::g_sched")

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
            cpus = provider.read(observed["sched.cpu"])

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
                snapshot.ElfRamProvider(ELF, ram_path, RAM_BASE, workbench_image.view())

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

            provider = snapshot.ElfRamProvider(ELF, ram_path, RAM_BASE, workbench_image.view())
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

            provider = snapshot.ElfRamProvider(ELF, ram_path, RAM_BASE, workbench_image.view())
            self.addCleanup(provider.close)
            top = RAM_BASE + (_observed_top() - RAM_BASE)
            with self.assertRaises(ValueError):
                provider.read_bytes(top - 8, 4096)
            with self.assertRaises(ValueError):
                provider.read_bytes(RAM_BASE - 4096, 4096)


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
        view = workbench_image.view()
        restored = pickle.loads(pickle.dumps(view))

        self.assertEqual(restored.resolved.keys(), view.resolved.keys())
        for topic, symbol in view.resolved.items():
            self.assertEqual(restored.resolved[topic], symbol)
        self.assertTrue(restored.symbols.has("nova::trace::g_ring"))

    def test_a_provider_given_a_view_never_opens_the_image(self):
        """Which is what lets the parse happen somewhere else: the
        provider that maps RAM is not the thing that read the ELF."""
        view = workbench_image.view()
        with tempfile.TemporaryDirectory() as directory:
            ram_path = Path(directory) / "guest-ram"
            with ram_path.open("wb") as ram:
                ram.truncate(_observed_top() - RAM_BASE)  # sparse
            provider = snapshot.ElfRamProvider(Path("/nonexistent.elf"), ram_path, RAM_BASE, view)
            self.addCleanup(provider.close)
            self.assertTrue(provider.symbols.has("nova::trace::g_ring"))


if __name__ == "__main__":
    unittest.main()
