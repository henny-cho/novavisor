"""The S layer: poll gating, change reporting, and RAM decoding."""

from __future__ import annotations

import struct
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from novakit.services.workbench import elfsym, hardware, snapshot  # noqa: E402
from novakit.services.workbench.observations import OBSERVATIONS, Obs  # noqa: E402

ELF = REPO / "build" / "aarch64-debug" / "novavisor.elf"
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
class ElfRamProviderTest(unittest.TestCase):
    """End-to-end address arithmetic against a synthetic RAM file."""

    def test_reads_a_seeded_scheduler_state(self):
        index = elfsym.ElfIndex(ELF)
        self.addCleanup(index.close)
        sched = index.resolve("nova::vcpu::g_sched")

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
                snapshot.ElfRamProvider(ELF, ram_path, RAM_BASE)


if __name__ == "__main__":
    unittest.main()
