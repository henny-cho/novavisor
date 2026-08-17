"""Descriptor encoding, read from the headers rather than restated."""

from __future__ import annotations

import unittest

from novakit.image import abi
from novakit.services.workbench import translation

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


# TCR_EL1 as two real guests programmed it, read off the hardware while
# each was running. Derivation is only interesting against values a guest
# actually chose — a hand-made TCR would test the arithmetic against
# itself.
LINUX_TCR = 0x0050_0074_B550_3510  # 4K, 48-bit both halves, TBI0|TBI1
BAREMETAL_TCR = 0x0080_3520  # 4K, 32-bit low half, high half disabled
KERNEL_VA = 0xFFFF_8000_807C_12A4


class GuestGeometryTest(unittest.TestCase):
    """A guest's Stage 1 is whatever its own TCR_EL1 says.

    EL2's geometry is a build constant and a guest's is not, so the two
    cannot share one. What they do share is the construction: the same
    checks run over a live register.
    """

    def test_a_linux_guest_walks_four_levels_in_both_halves(self):
        for high in (False, True):
            geometry = translation.guest_geometry(LINUX_TCR, high)
            with self.subTest(high=high):
                self.assertEqual(geometry.shifts, (39, 30, 21, 12))
                self.assertEqual(geometry.levels, (0, 1, 2, 3))
                self.assertEqual(geometry.address_bits, 48)
                self.assertTrue(geometry.tagged)  # TBI0 and TBI1 are both set
                self.assertTrue(translation.guest_half_enabled(LINUX_TCR, high))

    def test_a_narrow_guest_walks_three_levels_and_owns_no_high_half(self):
        low = translation.guest_geometry(BAREMETAL_TCR, high=False)
        self.assertEqual(low.levels, (1, 2, 3))
        self.assertEqual(low.address_bits, 32)
        self.assertFalse(low.tagged)
        self.assertTrue(translation.guest_half_enabled(BAREMETAL_TCR, high=False))
        # Not "no geometry": the half is described and switched off, and
        # a caller that conflated the two would offer a regime the guest
        # has told the hardware to fault on.
        self.assertFalse(translation.guest_half_enabled(BAREMETAL_TCR, high=True))

    def test_a_kernel_address_belongs_to_the_high_half_only(self):
        # The rule above the input width is a pattern, not a magnitude:
        # comparing sizes puts every kernel address out of range.
        high = translation.guest_geometry(LINUX_TCR, high=True)
        low = translation.guest_geometry(LINUX_TCR, high=False)
        self.assertTrue(high.holds(KERNEL_VA))
        self.assertFalse(low.holds(KERNEL_VA))
        self.assertEqual(high.within(KERNEL_VA), 0x8000_807C_12A4)
        self.assertEqual([high.index(high.within(KERNEL_VA), d) for d in range(4)], [256, 2, 3, 449])

    def test_a_tagged_pointer_lands_where_its_untagged_one_does(self):
        high = translation.guest_geometry(LINUX_TCR, high=True)
        tagged = KERNEL_VA | (0x5A << 56)
        self.assertTrue(high.holds(tagged))
        self.assertEqual(high.within(tagged), high.within(KERNEL_VA))

    def test_a_regime_without_tbi_refuses_a_tagged_pointer(self):
        low = translation.guest_geometry(BAREMETAL_TCR, high=False)
        self.assertTrue(low.holds(0x5000_2B20))
        self.assertFalse(low.holds(0x5000_2B20 | (0x5A << 56)))

    def test_a_reserved_granule_is_named_rather_than_decoded(self):
        # TCR_EL1 is the guest's register, so this is an input and not a
        # build fault: TG0 = 0b11 has no meaning to decode into.
        reserved = LINUX_TCR | (0b11 << 14)
        with self.assertRaises(ValueError):
            translation.guest_geometry(reserved, high=False)


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


# A guest names its tables by IPA. Offsetting the window by one 2 MiB
# block keeps the fake memory small while making the second walk visible:
# an answer that skipped Stage 2 would be off by exactly this much.
GUEST_IPA_OFFSET = MIB2


def ipa_of(pa: int) -> int:
    return pa + GUEST_IPA_OFFSET


def guest_window(memory: "Memory") -> int:
    """A Stage 2 root mapping this memory's IPA window onto it."""
    leaf = memory.table({(memory.base + GUEST_IPA_OFFSET) // MIB2 % 512: block(memory.base)})
    return memory.table({(memory.base + GUEST_IPA_OFFSET) // GIB: points_to(leaf)})


class ProbeTest(unittest.TestCase):
    """One address, followed down the way the hardware would."""

    def test_a_kernel_address_walks_its_guest_geometry(self):
        """The whole point of the high half: an address whose top bits are
        all ones is in range, and the walk indexes with what is under
        them. Comparing magnitudes refused this address before it read a
        single descriptor."""
        memory = Memory()
        geometry = translation.guest_geometry(LINUX_TCR, high=True)
        fmt = translation.replace(translation.STAGE1_FORMAT, geometry=geometry, wxn=False)
        # The type encoding is architectural, so the helpers above serve
        # both regimes; only the attribute bits are the regime's own.
        l3 = memory.table({449: page(0x507C_1000, S1["kAttrNormalRx"])})
        l2 = memory.table({3: points_to(l3)})
        l1 = memory.table({2: points_to(l2)})
        root = memory.table({256: points_to(l1)})
        found = translation.probe(memory, fmt, root, KERNEL_VA)
        self.assertEqual((found.fault, found.level), ("", 3))
        self.assertEqual(found.output, 0x507C_12A4)
        self.assertEqual([step.index for step in found.steps], [256, 2, 3, 449])

    def test_a_guest_table_is_itself_translated(self):
        """A guest's Stage 1 tables sit in its IPA space, so every level
        of the walk goes through Stage 2 first — the walk the hardware
        performs, in the shape the reader contract already takes."""
        memory = Memory()
        geometry = translation.guest_geometry(LINUX_TCR, high=True)
        fmt = translation.replace(translation.STAGE1_FORMAT, geometry=geometry, wxn=False)
        l3 = memory.table({449: page(0x507C_1000, S1["kAttrNormalRx"])})
        l2 = memory.table({3: points_to(ipa_of(l3))})
        l1 = memory.table({2: points_to(ipa_of(l2))})
        root = memory.table({256: points_to(ipa_of(l1))})
        guest = translation.GuestReader(memory, guest_window(memory))
        found = translation.probe(guest, fmt, ipa_of(root), KERNEL_VA)
        self.assertEqual((found.fault, found.level), ("", 3))
        self.assertEqual(found.output, 0x507C_12A4)
        # Every table was named by IPA and read at its physical address,
        # so an answer that skipped the second walk would differ.
        self.assertEqual(
            [step.table for step in found.steps],
            [ipa_of(root), ipa_of(l1), ipa_of(l2), ipa_of(l3)],
        )

    def test_a_guest_table_the_host_never_mapped_is_not_an_unmapped_page(self):
        """Two failures with different owners: the guest not mapping an
        address, and the host not mapping the guest's table. The
        architecture reports S1PTW beside the second, and so does this."""
        memory = Memory()
        geometry = translation.guest_geometry(LINUX_TCR, high=True)
        fmt = translation.replace(translation.STAGE1_FORMAT, geometry=geometry, wxn=False)
        root = memory.table({})
        unmapped = translation.GuestReader(memory, memory.table({}))
        found = translation.probe(unmapped, fmt, ipa_of(root), KERNEL_VA)
        self.assertEqual(found.fault, "s1ptw")
        self.assertEqual(found.level, geometry.levels[0])

        # The guest's own missing mapping still reads as a translation
        # fault, so the two are told apart rather than merged.
        walkable = translation.GuestReader(memory, guest_window(memory))
        self.assertEqual(translation.probe(walkable, fmt, ipa_of(root), KERNEL_VA).fault, "translation")

    def test_the_other_half_refuses_the_same_address(self):
        memory = Memory()
        low = translation.guest_geometry(LINUX_TCR, high=False)
        fmt = translation.replace(translation.STAGE1_FORMAT, geometry=low, wxn=False)
        found = translation.probe(memory, fmt, memory.table({}), KERNEL_VA)
        self.assertEqual((found.fault, found.steps), ("address-size", ()))

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
