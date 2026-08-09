"""Reading firmware constants out of the headers that define them.

The reader has to agree with the compiler, not merely with itself: a
number it guesses is a second copy of the encoding, right until the day
the header moves. So the cases here are the ones where C++ and Python
could plausibly part — a fixed-width complement, a negative division, a
suffix, a name declared elsewhere — plus the real headers whose own
static_asserts say what the folded value must be.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from novakit.image import abi  # noqa: E402
from novakit.services.workbench import translation  # noqa: E402


class ConstexprReaderTest(unittest.TestCase):
    def header(self, body: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "constants.hpp"
        path.write_text(body)
        return path

    def test_it_folds_what_the_compiler_static_asserts(self):
        """`stage1_tables.hpp` builds three register values out of its
        own named fields and asserts each against the #define the
        assembler uses. The compiler has proved those pairs equal, so a
        reader landing on the same numbers folds shifts, masks and
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
        """A guessed number becomes a second copy of the encoding, right
        until the day the header moves."""
        for expression in ("width(3)", "~kMask", "kA ? kB : kC", "1 +"):
            with self.assertRaises(SystemExit, msg=expression):
                abi.read_constexprs(self.header(f"inline constexpr int kX = {expression};\n"))

    def test_a_header_it_cannot_fully_fold_still_answers(self):
        """Asking for two plain constants out of a header must not be
        stopped by an expression nobody asked about — a fixed-width
        complement is the standing example, and headers that hold one
        also hold the bands a panel needs."""
        header = self.header(
            "inline constexpr std::uint32_t kMask = 0xFFF;\n"
            "inline constexpr std::uint32_t kUpper = ~kMask;\n"
            "inline constexpr std::uint32_t kLow = 1;\n"
        )
        self.assertEqual(
            abi.read_constexprs(header, wanted={"kMask", "kLow"}), {"kMask": 0xFFF, "kLow": 1}
        )
        with self.assertRaises(SystemExit):
            abi.read_constexprs(header)

    def test_a_name_that_is_not_there_is_an_error_not_a_gap(self):
        header = self.header("inline constexpr int kReal = 2;\n")
        with self.assertRaises(SystemExit):
            abi.read_constexprs(header, wanted={"kReal", "kNoSuchThing"})

    def test_a_division_the_two_languages_disagree_about_is_refused(self):
        # C++ truncates toward zero where Python floors, so they part on
        # a negative operand. Folding it anyway would be a plausible
        # number the firmware never had.
        header = self.header(
            "inline constexpr int kLow = 0 - 7;\n"
            "inline constexpr int kBand = kLow / 2;\n"
        )
        self.assertEqual(abi.read_constexprs(header, wanted={"kLow"}), {"kLow": -7})
        with self.assertRaises(SystemExit):
            abi.read_constexprs(header, wanted={"kBand"})

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


if __name__ == "__main__":
    unittest.main()
