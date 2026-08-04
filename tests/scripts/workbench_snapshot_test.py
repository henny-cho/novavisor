"""The S layer: poll gating, change reporting, and RAM decoding."""

from __future__ import annotations

import struct
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from novakit.services.workbench import elfsym, snapshot  # noqa: E402
from novakit.services.workbench.observations import OBSERVATIONS, Obs  # noqa: E402

ELF = REPO / "build" / "aarch64-debug" / "novavisor.elf"


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
                # Sparse; must span the PA-declared IVC page (+512 MiB),
                # which the provider's size check now covers too.
                ram.truncate(0x6000_1000 - snapshot.RAM_BASE)
                ram.seek(sched.address - snapshot.RAM_BASE)
                # CpuSched: current=1, fp=kNoOwner, fp_trap=1, idling=0
                ram.write(struct.pack("<QQ??6x", 1, (1 << 64) - 1, True, False))

            provider = snapshot.ElfRamProvider(ELF, ram_path)
            self.addCleanup(provider.close)
            observed = {obs.topic: obs for obs in OBSERVATIONS}
            cpus = provider.read(observed["sched.cpu"])

        self.assertEqual(
            cpus[0],
            {"current": 1, "fp": (1 << 64) - 1, "fp_trap": True, "idling": False},
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
                snapshot.ElfRamProvider(ELF, ram_path)


if __name__ == "__main__":
    unittest.main()
