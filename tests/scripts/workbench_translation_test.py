"""Descriptor encoding, read from the headers rather than restated."""

from __future__ import annotations

import sys
import tempfile
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


class ConstexprReaderTest(unittest.TestCase):
    """The reader has to agree with the compiler, not merely with itself."""

    def header(self, body: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "constants.hpp"
        path.write_text(body)
        return path

    def test_it_folds_what_the_compiler_static_asserts(self):
        """The strongest check available, and it costs nothing.

        `stage1_tables.hpp` builds three register values out of its own
        named fields and asserts each against the #define the assembler
        uses. The compiler has already proved those pairs equal, so a
        reader landing on the same numbers is folding shifts, masks and
        cross-references the way the compiler does.
        """
        folded = abi.read_constexprs(translation.STAGE1_TABLES)
        regs = REPO / "src" / "hal" / "arch" / "aarch64" / "vmsa" / "stage1_regs.h"
        for constant, define in (
            ("kMairEl2", "NOVA_EL2_MAIR"),
            ("kTcrEl2", "NOVA_EL2_TCR"),
            ("kSctlrEl2", "NOVA_EL2_SCTLR"),
        ):
            self.assertEqual(folded[constant], abi.read_define(regs, define), constant)

    def test_a_name_reaches_the_expressions_below_it(self):
        values = abi.read_constexprs(
            self.header(
                "inline constexpr std::uint64_t kShift = 12;\n"
                "inline constexpr std::uint64_t kSize  = 1ULL << kShift;\n"
            )
        )
        self.assertEqual(values, {"kShift": 12, "kSize": 4096})

    def test_a_name_from_elsewhere_is_supplied_not_guessed(self):
        values = abi.read_constexprs(
            self.header("inline constexpr std::size_t kEnd = kBase + 2;\n"), {"kBase": 7}
        )
        # Only what the header declares comes back; the seed was input.
        self.assertEqual(values, {"kEnd": 9})

    def test_an_undefined_name_stops_the_tool(self):
        with self.assertRaises(SystemExit):
            abi.read_constexprs(self.header("inline constexpr int kX = kNeverDeclared;\n"))

    def test_an_expression_it_cannot_fold_stops_the_tool(self):
        """A guessed number is worse than none: it becomes a second copy
        of the encoding, right until the day the header moves."""
        for expression in ("width(3)", "~kMask", "kA ? kB : kC", "1 +"):
            with self.assertRaises(SystemExit, msg=expression):
                abi.read_constexprs(self.header(f"inline constexpr int kX = {expression};\n"))

    def test_digit_separators_and_suffixes_are_not_part_of_the_value(self):
        values = abi.read_constexprs(
            self.header("inline constexpr std::uint64_t kMask = 0x0000'FFFF'FFFF'F000ULL;\n")
        )
        self.assertEqual(values["kMask"], 0xFFFF_FFFF_F000)

    def test_a_commented_out_declaration_is_not_read(self):
        values = abi.read_constexprs(
            self.header(
                "// inline constexpr int kGhost = 1;\n"
                "inline constexpr int kReal = 2; // inline constexpr int kAlso = 3;\n"
            )
        )
        self.assertEqual(values, {"kReal": 2})


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
            translation._geometry("bad", (30, 21, 12), 512, 39, 12, starts_at=2)

    def test_an_address_wider_than_the_top_level_is_rejected(self):
        with self.assertRaises(SystemExit):
            translation._geometry("bad", (30, 21, 12), 512, 48, 12)


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
        """The block encoding is reserved at L3. Read as a mapping it
        would be a window the hardware never opened."""
        raw = FRAME | S2["kAttrNormalRwx"] | S2["kTypeBlock"]
        decoded = translation.STAGE2_FORMAT.decode(raw, 2)
        self.assertEqual(decoded.kind, "invalid")
        self.assertFalse(decoded.maps)

    def test_an_unwritten_slot_is_invalid(self):
        self.assertEqual(translation.STAGE2_FORMAT.decode(0, 0).kind, "invalid")

    def test_el2_text_and_data_are_each_missing_what_the_other_has(self):
        """W^X, as the two presets encode it. The view exists to show
        this, and the decode is where it is either seen or lost."""
        text = translation.STAGE1_FORMAT.decode(FRAME | S1["kAttrNormalRx"] | S1["kTypeBlock"], 1)
        data = translation.STAGE1_FORMAT.decode(FRAME | S1["kAttrNormalRw"] | S1["kTypeBlock"], 1)
        self.assertEqual((text.writable, text.executable), (False, True))
        self.assertEqual((data.writable, data.executable), (True, False))

    def test_el2_memory_type_is_named_through_mair(self):
        raw = FRAME | S1["kAttrDevice"] | S1["kTypeBlock"]
        self.assertEqual(translation.STAGE1_FORMAT.decode(raw, 1).memory, "device-nGnRE")


if __name__ == "__main__":
    unittest.main()
