"""What the structure analyser reads, and what it refuses to guess.

Two lanes, because the two questions need different things. The rules
themselves — which mnemonic makes an edge, where an instruction belongs,
what a root is — are decided on synthetic disassembly text and run
anywhere. Whether those rules describe a real linked image is decided
against `callgraph.elf`, a fixture whose call graph is written by hand in
`tests/fixtures/elf/`, and needs the cross toolchain: those cases skip
where it is absent, the same shape `observe_test` uses for the debug ELF.

The oracle is never generated from the analyser. `callgraph.expect.json`
is written beside the fixture source and read here, so a change in the
analyser that also changes what it claims to have found fails.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from novakit.core import proc
from novakit.image import elfstruct
from novakit.services import ci
from tests import REPO

FIXTURE_SOURCE = REPO / "tests" / "fixtures" / "elf" / "callgraph.S"
EXPECTED = REPO / "tests" / "fixtures" / "elf" / "callgraph.expect.json"
# Built by every cross preset; any one of them proves the same graph.
FIXTURES = tuple(
    REPO / "build" / preset / "tests" / "callgraph.elf"
    for preset in ("aarch64-release", "aarch64-minimal-release", "aarch64-standard-release")
)
BUILT = next((path for path in FIXTURES if path.is_file()), None)


def _function(name: str, address: int, size: int) -> elfstruct.Function:
    return elfstruct.Function(name, address, size)


class AttributionTest(unittest.TestCase):
    """An instruction belongs to the extent that holds it, or to nothing."""

    functions = [
        _function("alpha", 0x1000, 0x10),
        _function("beta", 0x1010, 0x08),
        _function("gamma", 0x1020, 0x04),  # a hole at 0x1018..0x101f
    ]

    def test_an_address_inside_an_extent_names_its_function(self):
        self.assertEqual(elfstruct._containing(self.functions, 0x1000), "alpha")
        self.assertEqual(elfstruct._containing(self.functions, 0x100C), "alpha")
        self.assertEqual(elfstruct._containing(self.functions, 0x1010), "beta")

    def test_an_address_in_a_hole_or_past_the_end_names_nothing(self):
        self.assertIsNone(elfstruct._containing(self.functions, 0x1018))
        self.assertIsNone(elfstruct._containing(self.functions, 0x0FFF))
        self.assertIsNone(elfstruct._containing(self.functions, 0x1024))


class EdgeRuleTest(unittest.TestCase):
    """Which mnemonics make an edge, and which only make a count."""

    functions = [
        _function("caller", 0x1000, 0x20),
        _function("callee", 0x1020, 0x10),
    ]

    def walk(self, *lines: str):
        return elfstruct._walk("\n".join(lines), self.functions)

    def test_bl_is_an_edge(self):
        edges, indirect, _ = self.walk("    1000:\tbl\t1020 <callee>")
        self.assertEqual(edges, {"caller": {"callee"}})
        self.assertEqual(indirect, {})

    def test_a_branch_out_of_the_function_is_a_tail_edge(self):
        edges, _, branches = self.walk("    1004:\tb\t1020 <callee>")
        self.assertEqual(edges, {"caller": {"callee"}})
        self.assertEqual(branches, {"caller": ["callee"]})

    def test_a_branch_inside_the_function_is_a_loop_not_an_edge(self):
        edges, _, _ = self.walk("    1008:\tb\t1000 <caller>")
        self.assertEqual(edges, {})

    def test_a_register_branch_is_counted_and_never_followed(self):
        edges, indirect, _ = self.walk(
            "    100c:\tblr\tx1",
            "    1010:\tbr\tx2",
        )
        self.assertEqual(edges, {})
        self.assertEqual(indirect, {"caller": 2})

    def test_a_conditional_branch_is_not_an_edge(self):
        """-ffunction-sections keeps them inside one function by construction."""
        edges, indirect, _ = self.walk(
            "    1014:\tb.ne\t1020 <callee>",
            "    1018:\tcbz\tx0, 1020 <callee>",
            "    101c:\ttbz\tw0, #0, 1020 <callee>",
        )
        self.assertEqual(edges, {})
        self.assertEqual(indirect, {})

    def test_an_instruction_outside_every_extent_is_ignored(self):
        edges, indirect, _ = self.walk(
            "    2000:\tbl\t1020 <callee>",
            "    2004:\tblr\tx3",
        )
        self.assertEqual(edges, {})
        self.assertEqual(indirect, {})


class ChainPatternTest(unittest.TestCase):
    """The cib chains are roots because their address is stored, not branched to.

    Real demangled spellings: c++filt prints the return type ahead of a
    template function and omits it for an ordinary member, so the two
    chains do not share a prefix and the patterns must not assume one.
    """

    CALLBACK = (
        "void callback::builder<stdx::v1::tuple<void (*)(nova::MmioCall*) noexcept, "
        "void (*)(nova::MmioCall*) noexcept>, nova::MmioCall*>::run<cib::nexus<"
        "cib::top<nova::nova_project>::component> >(nova::MmioCall*)"
    )
    FLOW = (
        "flow::graph_builder<stdx::v1::ct_string<1ul>{}, flow::log_policies::none, "
        "flow::func_list>::built_flow<cib::initialized<cib::top<nova::nova_project>"
        "::component, cib::RuntimeStart>, cib::nexus<...> >::run()"
    )

    def matched(self, name: str) -> bool:
        return any(pattern.search(name) for pattern in elfstruct.CIB_CHAIN_PATTERNS)

    def test_both_chain_spellings_match(self):
        self.assertTrue(self.matched(self.CALLBACK))
        self.assertTrue(self.matched(self.FLOW))

    def test_an_ordinary_component_handler_does_not(self):
        self.assertFalse(self.matched("nova::vgic_component::handle_mmio(nova::MmioCall*)"))
        self.assertFalse(self.matched("nova::trap::dispatch_data_abort(nova::TrapContext*)"))


@unittest.skipUnless(BUILT is not None, "callgraph fixture not cross-built")
@unittest.skipUnless(importlib.util.find_spec("elftools"), "pyelftools is not installed")
class AgainstTheFixtureTest(unittest.TestCase):
    """The rules, read off a real linked image, against a hand-written oracle."""

    def test_the_analysis_is_what_the_oracle_says(self):
        expected = json.loads(EXPECTED.read_text())
        expected.pop("_comment", None)
        self.assertEqual(elfstruct.analyse(BUILT).as_dict(), expected)

    def test_the_same_image_answers_the_same_twice(self):
        first = elfstruct.analyse(BUILT).as_dict()
        second = elfstruct.analyse(BUILT).as_dict()
        self.assertEqual(first, second)

    def test_symbols_outside_the_analysed_set_are_ignored_not_rejected(self):
        """The fixture carries an OBJECT and a zero-size NOTYPE on purpose.

        Both are legal in a linked image, and a reader that rejected them
        would reject every image the linker builds.
        """
        source = FIXTURE_SOURCE.read_text()
        self.assertIn("fx_marker", source)  # NOTYPE, size 0
        self.assertIn("fx_data", source)  # OBJECT
        functions = elfstruct.analyse(BUILT).functions
        self.assertNotIn("fx_marker", functions)
        self.assertNotIn("fx_data", functions)


# The runtime lane's own preset set is the single source of which images
# ship; reading it here keeps this test from growing a second list.
IMAGES = tuple(
    (preset, REPO / "build" / preset / "novavisor.elf") for preset in ci.RUNTIME_PRESETS
)
FIELDS = ("roots", "functions", "edges", "indirect_sites", "reachable", "unproven")


@unittest.skipUnless(importlib.util.find_spec("elftools"), "pyelftools is not installed")
class AgainstTheShippedImagesTest(unittest.TestCase):
    """The rules survive the images that ship, not only the fixture.

    One subtest per preset, each skipped where that tree is not built, so
    the host lane reports honestly and the runtime lane — which builds all
    three — actually exercises them.
    """

    def test_every_shipped_image_answers_every_field(self):
        for preset, elf in IMAGES:
            with self.subTest(preset=preset):
                if not elf.is_file():
                    self.skipTest(f"{preset} not built")
                report = elfstruct.analyse(elf).as_dict()
                for field in FIELDS:
                    self.assertIn(field, report)
                self.assertTrue(report["functions"], "an image with no sized functions")
                # Roots are derived, so an image whose chains went unfound
                # would still report — and silently resolve almost nothing.
                self.assertIn("_vector_table", report["roots"])
                self.assertTrue(
                    any(name.startswith("_Z") for name in report["roots"]),
                    "no cib chain among the roots: the slot patterns stopped matching",
                )
                self.assertEqual(
                    set(report["reachable"]) | set(report["unproven"]),
                    set(report["functions"]),
                    "every function is either reachable or unproven",
                )

    def test_the_same_image_answers_the_same_twice(self):
        for preset, elf in IMAGES:
            with self.subTest(preset=preset):
                if not elf.is_file():
                    self.skipTest(f"{preset} not built")
                self.assertEqual(
                    elfstruct.analyse(elf).as_dict(), elfstruct.analyse(elf).as_dict()
                )

    def test_a_stripped_image_is_refused_not_answered(self):
        built = next((elf for _, elf in IMAGES if elf.is_file()), None)
        if built is None:
            self.skipTest("no image built")
        with tempfile.TemporaryDirectory() as directory:
            stripped = Path(directory) / "stripped.elf"
            shutil.copy(built, stripped)
            proc.run(["aarch64-none-elf-strip", "-s", str(stripped)])
            with self.assertRaises(elfstruct.ContractViolation):
                elfstruct.analyse(stripped)


if __name__ == "__main__":
    unittest.main()
