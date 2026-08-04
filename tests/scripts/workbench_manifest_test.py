"""The observation manifest resolved against the real debug image.

CI runs this authoritatively as the static lane's `manifest` step; here
it also guards local `nova test` runs whenever the ELF is present.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from novakit.services.workbench import checks, elfsym, observations  # noqa: E402

ELF = REPO / "build" / "aarch64-debug" / "novavisor.elf"


@unittest.skipUnless(ELF.is_file(), "debug ELF not built")
class ManifestResolutionTest(unittest.TestCase):
    def test_every_observation_resolves(self):
        self.assertEqual(checks.verify_manifest(ELF), 0)

    def test_scheduler_layout_matches_the_firmware(self):
        index = elfsym.ElfIndex(ELF)
        self.addCleanup(index.close)

        sched = index.resolve("nova::vcpu::g_sched")
        self.assertEqual(sched.type.count, observations.MAX_CPUS)
        names = [member.name for member in sched.type.element.fields]
        self.assertEqual(names, ["current", "fp", "fp_trap", "idling"])

        published = index.resolve("nova::vcpu::g_published_state")
        self.assertEqual(published.type.count, observations.MAX_VCPUS)
        labels = dict(published.type.element.enumerators)
        self.assertEqual(labels, {0: "kOff", 1: "kOnPending", 2: "kOn"})


if __name__ == "__main__":
    unittest.main()
