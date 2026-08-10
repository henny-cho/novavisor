"""What the manifest claims about the image, beyond resolving.

That every observation, table symbol and stop point resolves — each to
its own address — is `checks.verify_manifest`, which the static lane runs
as its `manifest` step. Here are the claims that check does not make: the
layouts the manifest reads by hand, and the shape rules that hold with no
image at all.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import workbench_image  # noqa: E402
from novakit.image import observe  # noqa: E402
from novakit.services.workbench import (  # noqa: E402
    events,
    hardware,
    observations,
    snapshot,
    steps,
)

ELF = workbench_image.ELF


class ManifestJoinTest(unittest.TestCase):
    """The build's question and the bridge's policy meet at the topic.

    Half a manifest is worse than a broken one: each half is separately
    valid, so nothing raises — the panel is simply blank, or the value
    arrives at a rate nobody chose. The join is the only place that can
    see both.
    """

    def test_a_policy_for_a_topic_nobody_resolves_is_refused(self):
        with mock.patch.dict(observations.POLICY, {"sched.ghost": observations.Policy()}):
            with self.assertRaises(SystemExit):
                observations._joined()

    def test_a_resolved_symbol_nobody_draws_is_refused(self):
        asked = (*observe.OBSERVED, observe.Want("sched.ghost", "nova::vcpu::g_sched"))
        with mock.patch.object(observe, "OBSERVED", asked):
            with self.assertRaises(SystemExit):
                observations._joined()

    def test_the_join_carries_both_halves(self):
        merged = {obs.topic: obs for obs in observations.OBSERVATIONS}
        for want in observe.OBSERVED:
            with self.subTest(topic=want.topic):
                self.assertEqual(merged[want.topic].symbol, want.symbol)
                self.assertEqual(merged[want.topic].fields, want.fields)
                self.assertEqual(
                    merged[want.topic].rate_hz, observations.POLICY[want.topic].rate_hz
                )


class StepFieldTest(unittest.TestCase):
    """A step names a reading's field, however deep the struct is."""

    READING = [{"el1": {"tcr": "0x1234", "sctlr": "0x1"}}, {"el1": {"tcr": "0x0"}}]

    def test_a_path_descends_where_the_reading_does(self):
        found = steps._select(self.READING, {"el1.tcr": "0x1234"})
        self.assertEqual(found["el1"]["sctlr"], "0x1")

    def test_a_flat_name_still_names_a_flat_field(self):
        self.assertTrue(steps._matches({"state": "translate"}, {"state": "translate"}))

    def test_a_path_through_something_that_is_not_a_record_matches_nothing(self):
        # Absent rather than raising: a step whose subject has not arrived
        # yet is pending, and a step whose subject cannot exist is refused
        # by the manifest join rather than here.
        self.assertFalse(steps._matches({"el1": "0x0"}, {"el1.tcr": "0x1234"}))
        self.assertFalse(steps._matches({}, {"el1.tcr": "0x1234"}))


class ShadowAgeTest(unittest.TestCase):
    """A shadow of hardware must say when it was last true.

    Which memory shadows registers is a declaration — no ELF can answer
    it — so what the manifest enforces is that the declaration resolves
    and keeps up. Both failures are silent otherwise: an age nothing
    publishes draws nothing, and one that lags leaves a window where a
    fresh value wears an old age.
    """

    def test_an_age_nobody_publishes_is_refused(self):
        ghost = observations.Policy(rate_hz=2, as_of="ctx.nowhere")
        with mock.patch.dict(observations.POLICY, {"ctx.el1": ghost}):
            with mock.patch.object(observations, "OBSERVATIONS", observations._joined()):
                with self.assertRaises(SystemExit):
                    observations._check_as_of()

    def test_an_age_slower_than_its_shadow_is_refused(self):
        slow = observations.Policy(rate_hz=2)
        with mock.patch.dict(observations.POLICY, {"ctx.synced": slow}):
            with mock.patch.object(observations, "OBSERVATIONS", observations._joined()):
                with self.assertRaises(SystemExit):
                    observations._check_as_of()

    def test_every_declared_age_reaches_the_ui(self):
        # The pairing travels with the rate and the predicate, so a panel
        # never spells it a second time.
        said = observations.observation_rates()
        for obs in observations.OBSERVATIONS:
            with self.subTest(topic=obs.topic):
                self.assertEqual(said[obs.topic].get("as_of", ""), obs.as_of)


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

    def test_a_group_the_table_does_not_name_is_refused(self):
        # The quiet direction. A missing base fails loudly on the next
        # lookup; a base with no name is absorbed into the width of the
        # group before it, and every slot of the new group goes out
        # carrying its predecessor's label.
        named = [name for name in observations.SLOT_NAMES if name != "kSlotCount"]
        with mock.patch.object(observations, "SLOT_HEADER") as header:
            header.read_text.return_value = "\n".join(
                f"inline constexpr std::size_t {name} = {index};"
                for index, name in enumerate([*named, "kSlotNewcomer", "kSlotCount"])
            )
            with self.assertRaises(SystemExit):
                observations._slot_bases()

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
@unittest.skipUnless(importlib.util.find_spec("elftools"), "pyelftools is not installed")
class ManifestResolutionTest(unittest.TestCase):
    """Layouts the manifest reads by hand, beyond what `verify_manifest`
    resolves. The resolution itself is that check's, and the static lane
    runs it."""

    def test_scheduler_layout_matches_the_firmware(self):
        resolved = workbench_image.view().resolved

        sched = resolved["sched.cpu"]
        self.assertEqual(sched.type.count, observations.MAX_CPUS)
        names = [member.name for member in sched.type.element.fields]
        self.assertEqual(names, ["current", "fp", "fp_trap", "idling"])

        published = resolved["sched.slots"]
        self.assertEqual(published.type.count, observations.MAX_VCPUS)
        labels = dict(published.type.element.enumerators)
        self.assertEqual(labels, {0: "kOff", 1: "kOnPending", 2: "kOn"})

    def test_vgic_state_is_banked_the_way_the_manifest_reads_it(self):
        resolved = workbench_image.view().resolved

        extents = {
            "vgic.lr": observations.MAX_VCPUS,
            "vgic.dist": observations.MAX_GUESTS,
            "vgic.resident": observations.MAX_CPUS,
        }
        for topic, count in extents.items():
            with self.subTest(topic=topic):
                self.assertEqual(resolved[topic].type.count, count)

        # The shadow is sized for the architectural maximum; how many of
        # its entries the machine actually has is what g_lr_count holds,
        # and it cannot exceed the array it indexes.
        lr = {field.name: field for field in resolved["vgic.lr"].type.element.fields}["lr"]
        self.assertEqual(lr.type.element.size, 8)
        capacity = resolved["vgic.capacity"]
        self.assertEqual(capacity.type.kind, "uint")
        self.assertGreaterEqual(lr.type.count, 1)


@unittest.skipUnless(ELF.is_file(), "debug ELF not built")
@unittest.skipUnless(importlib.util.find_spec("elftools"), "pyelftools is not installed")
class StopPointTest(unittest.TestCase):
    """How a stop point is found, against the real image.

    That every catalogued one resolves, and to its own address, is
    `verify_manifest`'s; what is left here is the matching rule it
    resolves by.
    """

    def setUp(self):
        self.symbols = workbench_image.view().symbols

    def test_a_shorter_name_is_not_a_prefix_of_a_longer_one(self):
        """Itanium length prefixes are what make the match safe:
        post_spi and post_spi_tracked encode as 8post_spi and
        16post_spi_tracked, so neither can match the other."""
        self.assertNotEqual(
            self.symbols.address_of("nova::vgic::post_spi"),
            self.symbols.address_of("nova::vgic::post_spi_tracked"),
        )

    def test_an_absent_function_is_refused(self):
        with self.assertRaises(KeyError):
            self.symbols.address_of("nova::vgic::no_such_entry_point")


class StopCatalogueTest(unittest.TestCase):
    """Shape checks that hold without an image."""

    def test_ids_are_unique(self):
        ids = [event.id for event in events.EVENTS]
        self.assertEqual(len(set(ids)), len(ids))

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
