"""Descriptor encoding, read from the headers rather than restated."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from novakit.image import abi  # noqa: E402
from novakit.services.workbench import translation  # noqa: E402

# The firmware's own presets and type encodings. Spelled out here they
# would be the second copy this module exists to avoid, and the test
# would keep passing after the header moved.
S2 = abi.read_constexprs(translation.STAGE2_DESCRIPTOR)
S1 = abi.read_constexprs(translation.STAGE1_TABLES)
FRAME = 0x8000_0000  # any 4 KiB-aligned output address


class GeometryTest(unittest.TestCase):
    def test_both_regimes_walk_three_levels_from_l1(self):
        for geometry in (translation.STAGE2, translation.STAGE1):
            self.assertEqual(geometry.levels, (1, 2, 3), geometry.name)
            self.assertEqual(geometry.table_bytes, 4096, geometry.name)

    def test_each_level_reads_its_own_index_field(self):
        # One entry into every level at once: each index must come back
        # as 1, and any two fields overlapping would show here.
        address = sum(1 << shift for shift in translation.STAGE2.shifts)
        self.assertEqual(
            [translation.STAGE2.index(address, depth) for depth in range(3)], [1, 1, 1]
        )

    def test_the_top_level_covers_exactly_the_stage2_input_address(self):
        """T0SZ and the level shifts are separate declarations of one
        geometry. A table's worth of L1 entries has to be the whole IPA
        space — larger and the walk starts a level too low."""
        top = translation.STAGE2
        self.assertEqual(top.span(0) * top.entries, 1 << top.address_bits)

    def test_el2_reaches_the_top_of_its_own_map(self):
        # T0SZ=32 leaves four 1 GiB entries in use out of the table's 512.
        self.assertEqual(translation.STAGE1.index((1 << 32) - 1, 0), 3)

    def test_a_declared_start_level_must_match_the_address_width(self):
        """T0SZ and SL0 are two declarations of one geometry and the CPU
        believes the second. A build that changed one alone would walk
        tables built for the other — a fault with no message."""
        with self.assertRaises(SystemExit):
            translation._geometry("bad", (30, 21, 12), 512, 39, starts_at=2)

    def test_an_address_wider_than_the_top_level_is_rejected(self):
        with self.assertRaises(SystemExit):
            translation._geometry("bad", (30, 21, 12), 512, 48)


class DescriptorTest(unittest.TestCase):
    """The firmware's own presets, decoded back into what they mean."""

    def test_guest_ram_is_writable_and_executable(self):
        raw = FRAME | S2["kAttrNormalRwx"] | S2["kTypeBlock"]
        block = translation.STAGE2_FORMAT.decode(raw, 0)
        self.assertEqual((block.kind, block.output), ("block", FRAME))
        self.assertTrue(block.writable and block.executable and block.accessed)
        self.assertEqual(block.memory, "normal-wb")

    def test_guest_mmio_is_device_memory_and_never_executable(self):
        raw = FRAME | S2["kAttrDeviceRw"] | S2["kTypePage"]
        page = translation.STAGE2_FORMAT.decode(raw, 2)
        self.assertEqual(page.kind, "page")
        self.assertEqual(page.memory, "device-nGnRE")
        self.assertTrue(page.writable)
        self.assertFalse(page.executable)

    def test_a_table_descriptor_points_and_permits_nothing(self):
        table = translation.STAGE2_FORMAT.decode(FRAME | S2["kTypeTable"], 0)
        self.assertEqual((table.kind, table.output), ("table", FRAME))
        # Permissions live at the leaf; reporting them here would be a
        # reading of bits the hardware does not consult at this level.
        self.assertFalse(table.writable or table.executable or table.memory)

    def test_the_same_two_bits_are_a_page_at_the_last_level(self):
        raw = FRAME | S2["kAttrNormalRwData"] | S2["kTypeTable"]
        self.assertEqual(translation.STAGE2_FORMAT.decode(raw, 1).kind, "table")
        self.assertEqual(translation.STAGE2_FORMAT.decode(raw, 2).kind, "page")

    def test_a_block_encoding_at_the_last_level_maps_nothing(self):
        """The block encoding is reserved at L3; read as a mapping it
        would be a window the hardware never opened."""
        raw = FRAME | S2["kAttrNormalRwx"] | S2["kTypeBlock"]
        decoded = translation.STAGE2_FORMAT.decode(raw, 2)
        self.assertEqual(decoded.kind, "invalid")
        self.assertFalse(decoded.maps)

    def test_an_unwritten_slot_is_invalid(self):
        self.assertEqual(translation.STAGE2_FORMAT.decode(0, 0).kind, "invalid")

    def test_el2_text_and_data_are_each_missing_what_the_other_has(self):
        """W^X as the two presets encode it — the decode is where it is
        either seen or lost."""
        text = translation.STAGE1_FORMAT.decode(FRAME | S1["kAttrNormalRx"] | S1["kTypeBlock"], 1)
        data = translation.STAGE1_FORMAT.decode(FRAME | S1["kAttrNormalRw"] | S1["kTypeBlock"], 1)
        self.assertEqual((text.writable, text.executable), (False, True))
        self.assertEqual((data.writable, data.executable), (True, False))

    def test_el2_memory_type_is_named_through_mair(self):
        raw = FRAME | S1["kAttrDevice"] | S1["kTypeBlock"]
        self.assertEqual(translation.STAGE1_FORMAT.decode(raw, 1).memory, "device-nGnRE")

    def test_only_el2_forbids_writable_and_executable(self):
        """Which regime forbids the pair is the firmware's answer, not a
        judgement made here: EL2 sets SCTLR_EL2.WXN, and a guest's Stage 2
        grants both on purpose so its own Stage 1 can do the splitting."""
        self.assertTrue(translation.STAGE1_FORMAT.wxn)
        self.assertFalse(translation.STAGE2_FORMAT.wxn)
        self.assertTrue(S1["kSctlrEl2"] & S1["kSctlrWxn"])


GIB = translation.STAGE2.span(0)
MIB2 = translation.STAGE2.span(1)
KIB4 = translation.STAGE2.span(2)


class Memory:
    """Tables written by hand, served the way RAM is.

    Small enough that an address the walk should never reach is outside
    it, which is how an unreadable table gets into these tests.
    """

    def __init__(self, base: int = 0x4000_0000, size: int = 64 * 1024):
        self.base = base
        self._bytes = bytearray(size)
        self._next = base

    def table(self, entries: dict[int, int]) -> int:
        pa = self._next
        self._next += translation.STAGE2.table_bytes
        for index, word in entries.items():
            at = pa - self.base + index * translation.DESCRIPTOR_BYTES
            self._bytes[at : at + 8] = word.to_bytes(8, "little")
        return pa

    def read_bytes(self, pa: int, size: int) -> bytes:
        offset = pa - self.base
        if offset < 0 or size < 0 or offset + size > len(self._bytes):
            raise ValueError(f"{pa:#x}+{size:#x} is outside this memory")
        return bytes(self._bytes[offset : offset + size])


def block(output: int, attrs: int | None = None) -> int:
    return output | (S2["kAttrNormalRwx"] if attrs is None else attrs) | S2["kTypeBlock"]


def page(output: int, attrs: int | None = None) -> int:
    return output | (S2["kAttrNormalRwData"] if attrs is None else attrs) | S2["kTypePage"]


def points_to(table: int) -> int:
    return table | S2["kTypeTable"]


class ProbeTest(unittest.TestCase):
    """One address, followed down the way the hardware would."""

    def test_a_gigabyte_block_answers_at_the_first_level(self):
        memory = Memory()
        root = memory.table({2: block(0x8000_0000)})
        found = translation.probe(memory, translation.STAGE2_FORMAT, root, 2 * GIB + 0x1234)
        self.assertEqual(found.output, 0x8000_0000 + 0x1234)
        self.assertEqual((found.level, found.fault), (1, ""))
        self.assertEqual(len(found.steps), 1)

    def test_the_offset_into_a_block_comes_from_the_input_address(self):
        """A block's output field is ignored below the block size, so the
        low bits of the answer are the ones that were asked about."""
        memory = Memory()
        leaf = memory.table({1: block(0x9020_0000)})
        root = memory.table({0: points_to(leaf)})
        found = translation.probe(memory, translation.STAGE2_FORMAT, root, MIB2 + 0x1FFFF)
        self.assertEqual(found.output, 0x9020_0000 + 0x1FFFF)
        self.assertEqual(found.level, 2)

    def test_a_full_descent_reaches_a_page(self):
        memory = Memory()
        l3 = memory.table({3: page(0xA000_3000)})
        l2 = memory.table({0: points_to(l3)})
        l1 = memory.table({0: points_to(l2)})
        found = translation.probe(memory, translation.STAGE2_FORMAT, l1, 3 * KIB4 + 0x40)
        self.assertEqual(found.output, 0xA000_3040)
        self.assertEqual([step.index for step in found.steps], [0, 0, 3])
        self.assertEqual([step.table for step in found.steps], [l1, l2, l3])

    def test_an_empty_slot_faults_at_the_level_that_holds_it(self):
        """The level is the number ESR_EL2 reports, which is what makes
        this answer comparable with a fault the guest actually took."""
        memory = Memory()
        l2 = memory.table({})
        l1 = memory.table({0: points_to(l2)})
        found = translation.probe(memory, translation.STAGE2_FORMAT, l1, MIB2)
        self.assertEqual((found.fault, found.level, found.output), ("translation", 2, None))
        # The path taken is still worth having: it says how far it got.
        self.assertEqual(len(found.steps), 2)

    def test_an_address_wider_than_the_regime_never_starts(self):
        memory = Memory()
        root = memory.table({0: block(0)})
        found = translation.probe(
            memory, translation.STAGE2_FORMAT, root, 1 << translation.STAGE2.address_bits
        )
        self.assertEqual((found.fault, found.steps), ("address-size", ()))

    def test_a_table_outside_memory_is_unreadable_not_unmapped(self):
        memory = Memory()
        root = memory.table({0: points_to(0xDEAD_0000)})
        found = translation.probe(memory, translation.STAGE2_FORMAT, root, 0)
        self.assertEqual(found.fault, "unreadable")

    def test_el2_walks_its_own_regime_the_same_way(self):
        memory = Memory()
        root = memory.table({1: 0x4000_0000 | S1["kAttrNormalRx"] | S1["kTypeBlock"]})
        found = translation.probe(memory, translation.STAGE1_FORMAT, root, GIB + 0x80)
        self.assertEqual(found.output, 0x4000_0080)
        self.assertFalse(found.steps[-1].descriptor.writable)


class TreeTest(unittest.TestCase):
    """Everything a root maps, folded the way the builder wrote it."""

    def test_a_mapped_region_arrives_as_one_run(self):
        memory = Memory()
        l2 = memory.table({index: block(0x8000_0000 + index * MIB2) for index in range(8)})
        l1 = memory.table({0: points_to(l2)})
        found = translation.tree(memory, translation.STAGE2_FORMAT, l1, limit=8)
        (top,) = found.nodes
        (run,) = top.children
        self.assertEqual((run.index, run.count, run.base), (0, 8, 0))
        self.assertEqual(run.descriptor.output, 0x8000_0000)

    def test_a_gap_in_the_output_breaks_the_run(self):
        memory = Memory()
        l2 = memory.table({0: block(0x8000_0000), 1: block(0x9000_0000)})
        l1 = memory.table({0: points_to(l2)})
        found = translation.tree(memory, translation.STAGE2_FORMAT, l1, limit=8)
        self.assertEqual([node.count for node in found.nodes[0].children], [1, 1])

    def test_a_changed_attribute_breaks_the_run(self):
        """Two regions the builder mapped differently are two regions, and
        folding them would report permissions over an address that never
        had them."""
        memory = Memory()
        l2 = memory.table(
            {
                0: block(0x8000_0000),
                1: block(0x8020_0000, S2["kAttrNormalRwData"]),
            }
        )
        l1 = memory.table({0: points_to(l2)})
        found = translation.tree(memory, translation.STAGE2_FORMAT, l1, limit=8)
        first, second = found.nodes[0].children
        self.assertEqual((first.count, second.count), (1, 1))
        self.assertTrue(first.descriptor.executable)
        self.assertFalse(second.descriptor.executable)

    def test_a_bit_this_module_does_not_decode_still_breaks_the_run(self):
        """The contiguous hint is not read anywhere here. Folded over, the
        run would claim a shape the builder did not write."""
        memory = Memory()
        l2 = memory.table(
            {
                0: block(0x8000_0000),
                1: block(0x8020_0000) | S2["kContigBit"],
            }
        )
        l1 = memory.table({0: points_to(l2)})
        found = translation.tree(memory, translation.STAGE2_FORMAT, l1, limit=8)
        self.assertEqual([node.count for node in found.nodes[0].children], [1, 1])

    def test_empty_slots_are_absent_rather_than_listed(self):
        memory = Memory()
        l1 = memory.table({7: block(0x8000_0000)})
        found = translation.tree(memory, translation.STAGE2_FORMAT, l1, limit=8)
        (only,) = found.nodes
        self.assertEqual((only.index, only.base), (7, 7 * GIB))

    def test_a_walk_stops_at_the_pool_it_was_built_from(self):
        """A word that is not a table pointer would otherwise send the
        reader through every table-shaped thing it can reach."""
        memory = Memory()
        leaves = [memory.table({0: block(0x8000_0000)}) for _ in range(4)]
        l1 = memory.table({index: points_to(leaf) for index, leaf in enumerate(leaves)})
        found = translation.tree(memory, translation.STAGE2_FORMAT, l1, limit=3)
        self.assertTrue(found.truncated)
        self.assertEqual(found.read, 3)
        # Two levels reached, the rest left empty rather than guessed at.
        self.assertEqual([len(node.children) for node in found.nodes], [1, 1, 0, 0])

    def test_a_table_it_could_not_read_is_named(self):
        """A short recording would otherwise come back as a smaller map
        with nothing to say it was short."""
        memory = Memory()
        l1 = memory.table({0: points_to(0xDEAD_0000)})
        found = translation.tree(memory, translation.STAGE2_FORMAT, l1, limit=8)
        self.assertEqual(found.unreadable, (0xDEAD_0000,))
        self.assertFalse(found.truncated)
        self.assertEqual(found.nodes[0].children, ())


if __name__ == "__main__":
    unittest.main()
