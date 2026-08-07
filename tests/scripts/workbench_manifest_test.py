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
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from novakit.services.workbench import (  # noqa: E402
    checks,
    elfsym,
    events,
    hardware,
    observations,
    paths,
    snapshot,
)

ELF = REPO / "build" / "aarch64-debug" / "novavisor.elf"


class TimerSlotTest(unittest.TestCase):
    """Slot labels follow the header that allocates the slots."""

    def test_each_group_starts_where_the_header_says(self):
        # The existing extent check only compares totals, so a group
        # inserted or reordered leaves the count right and every label
        # after it wrong by a slot. Nothing else would notice.
        bases = observations._slot_bases()
        labels = observations.timer_slot_labels()
        self.assertEqual(len(labels), bases["kSlotCount"])
        for name, base in bases.items():
            if name == "kSlotCount":
                continue
            with self.subTest(group=name):
                word = observations.SLOT_NAMES[name].split(" ")[0]
                self.assertTrue(labels[base].startswith(word), f"{labels[base]} at {base}")

    def test_a_base_the_reader_cannot_evaluate_is_refused(self):
        # Silence here would mean labels quietly shifted, so an
        # expression outside the plain-sum form has to stop the bridge.
        with mock.patch.object(observations, "SLOT_HEADER") as header:
            header.read_text.return_value = (
                "inline constexpr std::size_t kSlotSlice = kUnknownThing * 2;\n"
            )
            with self.assertRaises(SystemExit):
                observations._slot_bases()


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

    def test_the_syndrome_vocabulary_comes_from_the_firmware_enum(self):
        # The UI names an exception class from this enum and nothing
        # else. If an optimisation drops the type from the image the
        # names quietly become numbers, so the lookup fails here first.
        from novakit.services.workbench import derive

        labels = derive.syndrome_vocabulary(ELF)["esr_ec"]
        self.assertGreater(len(labels), 10)
        # Two the board leans on: the guest gate and the MMIO trap path.
        self.assertEqual(labels[0x16], "kHvcAa64")
        self.assertEqual(labels[0x24], "kDataAbortLower")

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


@unittest.skipUnless(ELF.is_file(), "debug ELF not built")
class StopPointTest(unittest.TestCase):
    """Every catalogued stop point must be a real function in the image.

    An inlined or renamed one leaves the UI offering a breakpoint that
    can never be hit — the halt-layer equivalent of a blank panel.
    """

    def setUp(self):
        self.index = elfsym.ElfIndex(ELF)
        self.addCleanup(self.index.close)

    def test_every_event_resolves_to_an_entry_address(self):
        for event in events.EVENTS:
            with self.subTest(event=event.id):
                address = self.index.resolve_function(event.symbol)
                self.assertGreater(address, 0)

    def test_addresses_are_distinct(self):
        """Two events at one address would be one stop wearing two
        names: arming either would fire both."""
        seen = {event.id: self.index.resolve_function(event.symbol) for event in events.EVENTS}
        self.assertEqual(len(set(seen.values())), len(seen), seen)

    def test_the_bind_carries_the_binding_in_its_arguments(self):
        """The whole point of the catalogue's first entry: the physical
        and virtual numbers are AAPCS64 arguments, so a stop there reads
        them off x0..x3 without decoding any memory."""
        bind = events.BY_ID["vgic.bind"]
        self.assertEqual(bind.args, ("vm", "vintid", "pintid", "generation"))

    def test_resolution_needs_no_debug_info(self):
        """`.symtab` alone. A function's parameters live in its mangled
        name, and the prefix match sidesteps having to spell them."""
        prefix = elfsym.mangle("nova::vgic::post_spi_tracked")
        self.assertEqual(prefix, "_ZN4nova4vgic16post_spi_trackedE")
        self.assertEqual(
            self.index.resolve_function("nova::vgic::post_spi_tracked"),
            self.index.resolve_function("nova::vgic::post_spi_tracked"),
        )

    def test_a_shorter_name_is_not_a_prefix_of_a_longer_one(self):
        """Itanium length prefixes are what make the match safe:
        post_spi and post_spi_tracked encode as 8post_spi and
        16post_spi_tracked, so neither can match the other."""
        self.assertNotEqual(
            self.index.resolve_function("nova::vgic::post_spi"),
            self.index.resolve_function("nova::vgic::post_spi_tracked"),
        )

    def test_an_absent_function_is_refused(self):
        with self.assertRaises(KeyError):
            self.index.resolve_function("nova::vgic::no_such_entry_point")


class StopCatalogueTest(unittest.TestCase):
    """Shape checks that hold without an image."""

    def test_ids_are_unique(self):
        ids = [event.id for event in events.EVENTS]
        self.assertEqual(len(set(ids)), len(ids))

    def test_every_edge_named_is_a_published_path(self):
        known = {edge.id for edge in paths.EDGES}
        for event in events.EVENTS:
            if event.edge:
                with self.subTest(event=event.id):
                    self.assertIn(event.edge, known)

    def test_the_catalogue_ships_no_addresses(self):
        """Addresses change every build and the UI has no use for one;
        shipping them would invite a client to cache a stale map.

        A record's code and field names are the opposite kind of fact:
        fixed by the ABI header both the ring writer and this reader
        compile against, and needed because a column-encoded record
        carries nothing else to look itself up by or name its words
        with.
        """
        for entry in events.catalogue():
            self.assertEqual(set(entry), {"id", "edge", "args", "label", "code", "fields"})

    def test_every_record_word_the_bridge_decodes_is_also_named(self):
        """decode() knows the packing; the catalogue names the words.
        An event whose words go unnamed reaches a reader who clicked a
        mark as three bare numbers."""
        for event in events.EVENTS:
            with self.subTest(event=event.id):
                self.assertEqual(len(event.fields), 3)
                self.assertTrue(event.fields[0], f"{event.id} names none of its words")


if __name__ == "__main__":
    unittest.main()
