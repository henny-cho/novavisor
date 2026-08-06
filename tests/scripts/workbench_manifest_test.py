"""The observation manifest resolved against the real debug image.

CI runs this authoritatively as the static lane's `manifest` step; here
it also guards local `nova test` runs whenever the ELF is present.
"""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from novakit.services.workbench import (  # noqa: E402
    checks,
    elfsym,
    hardware,
    observations,
    snapshot,
)

ELF = REPO / "build" / "aarch64-debug" / "novavisor.elf"


class PageLayoutTest(unittest.TestCase):
    """Guest memory carries no DWARF, so the one layout the decoder is
    told rather than shown has to be held to the board map instead."""

    def test_a_pa_declared_page_sits_in_a_published_region(self):
        # An address the bridge reads that the board never claims is a
        # window into whatever happens to be there.
        regions = hardware.board_map()["regions"]["pa"]
        for obs in observations.OBSERVATIONS:
            if obs.pa is None:
                continue
            size = snapshot.PAGE_LAYOUTS[obs.layout].size
            with self.subTest(topic=obs.topic):
                home = [
                    region
                    for region in regions
                    if region["base"] <= obs.pa
                    and obs.pa + size <= region["base"] + region["size"]
                ]
                self.assertEqual(len(home), 1, f"{obs.pa:#x} +{size:#x} is in no one region")
                self.assertEqual(home[0]["kind"], hardware.KIND_SHARED)

    def test_the_ivc_rings_tile_the_shared_page(self):
        # Both rings inside the page, neither overlapping the other: the
        # firmware's own invariant, checked against its own header.
        page = snapshot.PAGE_LAYOUTS["ivc_ring_page"]
        rings = sorted(page.fields, key=lambda field: field.offset)
        self.assertEqual([field.name for field in rings], ["ring0", "ring1"])
        for ahead, behind in zip(rings, rings[1:], strict=False):
            self.assertLessEqual(ahead.offset + ahead.type.size, behind.offset)
        last = rings[-1]
        self.assertLessEqual(last.offset + last.type.size, page.size)
        slots = {field.name: field for field in last.type.fields}["slots"]
        # Capacity == slot count, and the indices wrap by truncation.
        self.assertEqual(slots.type.count & (slots.type.count - 1), 0)


@unittest.skipUnless(ELF.is_file(), "debug ELF not built")
class ManifestResolutionTest(unittest.TestCase):
    def test_every_observation_resolves(self):
        self.assertEqual(checks.verify_manifest(ELF), 0)

    def test_symbols_report_covers_every_topic(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(checks.describe_symbols(ELF), 0)
        lines = output.getvalue().splitlines()
        topics = {line.split()[0] for line in lines[1:]}
        self.assertEqual(topics, {obs.topic for obs in observations.OBSERVATIONS})

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

    def test_vgic_state_is_banked_the_way_the_manifest_reads_it(self):
        index = elfsym.ElfIndex(ELF)
        self.addCleanup(index.close)

        extents = {
            "nova::vgic::(anonymous)::g_cpu": observations.MAX_VCPUS,
            "nova::vgic::(anonymous)::g_dist": observations.MAX_GUESTS,
            "nova::vgic::(anonymous)::g_resident": observations.MAX_CPUS,
        }
        for symbol, count in extents.items():
            with self.subTest(symbol=symbol):
                self.assertEqual(index.resolve(symbol).type.count, count)

        # The shadow is sized for the architectural maximum; how many of
        # its entries the machine actually has is what g_lr_count holds,
        # and it cannot exceed the array it indexes.
        lr = {
            field.name: field
            for field in index.resolve("nova::vgic::(anonymous)::g_cpu").type.element.fields
        }["lr"]
        self.assertEqual(lr.type.element.size, 8)
        capacity = index.resolve("nova::vgic::(anonymous)::g_lr_count")
        self.assertEqual(capacity.type.kind, "uint")
        self.assertGreaterEqual(lr.type.count, 1)


if __name__ == "__main__":
    unittest.main()
