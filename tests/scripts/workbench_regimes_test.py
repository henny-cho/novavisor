"""The page tables a run has, copied once and walked from the copy."""

from __future__ import annotations

import struct
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from novakit.services.workbench import (  # noqa: E402
    hardware,
    regimes,
    snapshot,
    translation,
)
from novakit.services.workbench.observations import OBSERVATIONS  # noqa: E402

ELF = REPO / "build" / "aarch64-debug" / "novavisor.elf"
RAM_BASE = hardware.platform()["NOVA_BOARD_PHYS_RAM_BASE"]
S2 = translation.STAGE2_FORMAT


def _ram_size() -> int:
    """As far above the base as any observation reaches."""
    return max(
        obs.pa + snapshot.PAGE_LAYOUTS[obs.layout].size
        for obs in OBSERVATIONS
        if obs.pa is not None
    ) - RAM_BASE


@unittest.skipUnless(ELF.is_file(), "debug ELF not built")
class CaptureTest(unittest.TestCase):
    """Against the real image: the field offsets are the DWARF's."""

    @classmethod
    def setUpClass(cls):
        # One DWARF walk for the module; it is three seconds of work and
        # the answer cannot change between these tests.
        cls.view = snapshot.resolve_image(ELF)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.ram_path = Path(self.directory.name) / "guest-ram"
        self.ram = bytearray(_ram_size())
        self.symbols = self.view.regimes

    def poke(self, pa: int, *words: int) -> None:
        at = pa - RAM_BASE
        self.ram[at : at + 8 * len(words)] = struct.pack(f"<{len(words)}Q", *words)

    def provider(self):
        self.ram_path.write_bytes(bytes(self.ram))
        made = snapshot.ElfRamProvider(ELF, self.ram_path, RAM_BASE, self.view)
        self.addCleanup(made.close)
        return made

    def field(self, symbol: str, name: str) -> int:
        entry = self.symbols[symbol]
        info = entry.type.element if entry.type.kind == "array" else entry.type
        return next(member.offset for member in info.fields if member.name == name)

    def build_guest_tables(self) -> tuple[int, int]:
        """One guest window: an L1 table entry over four 2 MiB blocks."""
        sets = self.symbols["nova::(anonymous)::g_stage2_sets"]
        vttbr = self.symbols["nova::(anonymous)::g_vttbr"]
        l1 = sets.address + self.field("nova::(anonymous)::g_stage2_sets", "l1")
        l2 = sets.address + self.field("nova::(anonymous)::g_stage2_sets", "l2_pool")
        self.poke(l1, l2 | S2.type_mask)
        for slot in range(4):
            self.poke(l2 + slot * 8, (0x8000_0000 + slot * translation.STAGE2.span(1)) | 0x7FC | 1)
        # VTTBR carries the VMID above the table address; the walk starts
        # at the address alone.
        self.poke(vttbr.address, l1 | (1 << 48))
        return l1, l2

    def test_nothing_is_published_before_el2_builds_the_tables(self):
        """The RAM backend exists from the moment QEMU starts. A read
        before the build would copy a page of zeros and publish it as the
        machine's whole address map."""
        self.assertIsNone(regimes.capture(self.provider(), self.symbols))

    def test_a_built_guest_becomes_a_regime_rooted_where_vttbr_points(self):
        l1, _ = self.build_guest_tables()
        captured = regimes.capture(self.provider(), self.symbols)
        cpu = next(entry for entry in captured["regimes"] if entry["id"] == "vm0.cpu")
        self.assertEqual(int(cpu["root"], 16), l1)
        self.assertEqual((cpu["kind"], cpu["vm"], cpu["role"]), ("stage2", 0, "cpu"))

    def test_the_walk_budget_is_the_pool_the_tables_were_built_from(self):
        self.build_guest_tables()
        captured = regimes.capture(self.provider(), self.symbols)
        found = {entry["id"]: entry["tables"] for entry in captured["regimes"]}
        sets = self.symbols["nova::(anonymous)::g_stage2_sets"].type.element
        el2 = self.symbols["nova_el2_l1_root"].size + self.symbols["(anonymous)::g_pool"].size
        self.assertEqual(found["vm0.cpu"], sets.size // translation.STAGE2.table_bytes)
        self.assertEqual(found["el2"], el2 // translation.STAGE1.table_bytes)

    def test_dma_regimes_come_from_the_contexts_the_smmu_built(self):
        self.build_guest_tables()
        contexts = self.symbols["nova::smmu::(anonymous)::g_contexts"]
        tables = self.symbols["nova::smmu::(anonymous)::g_dma_tables"]
        root = tables.address + self.field("nova::smmu::(anonymous)::g_dma_tables", "l1")
        entry = contexts.address
        self.poke(entry + self.field("nova::smmu::(anonymous)::g_contexts", "owner_vm"), 0)
        self.poke(entry + self.field("nova::smmu::(anonymous)::g_contexts", "root_pa"), root)
        self.poke(self.symbols["nova::smmu::(anonymous)::g_context_count"].address, 1)

        captured = regimes.capture(self.provider(), self.symbols)
        dma = next(entry for entry in captured["regimes"] if entry["id"] == "vm0.dma")
        self.assertEqual(int(dma["root"], 16), root)
        # Two stage-2 translations of one VM, side by side rather than one
        # over the other: the difference between them is the isolation.
        cpu = next(entry for entry in captured["regimes"] if entry["id"] == "vm0.cpu")
        self.assertNotEqual(dma["root"], cpu["root"])

    def test_a_run_with_no_smmu_has_no_dma_regimes(self):
        self.build_guest_tables()
        captured = regimes.capture(self.provider(), self.symbols)
        self.assertEqual([entry for entry in captured["regimes"] if entry["role"] == "dma"], [])

    def test_the_copy_answers_exactly_what_ram_did(self):
        """The one claim the whole capture rests on. A copy that answers
        differently would make a replay a second program that reimplements
        the walk's input."""
        l1, _ = self.build_guest_tables()
        live = self.provider()
        captured = regimes.capture(live, self.symbols)
        copy = regimes.Tables.of(captured)

        from_ram = translation.tree(live, S2, l1, tables=6)
        from_copy = translation.tree(copy, S2, l1, tables=6)
        self.assertEqual(from_ram, from_copy)
        # And not vacuously: the guest window is in there.
        (top,) = from_ram.nodes
        (run,) = top.children
        self.assertEqual(run.count, 4)

    def test_only_the_words_that_are_set_travel(self):
        """A table is almost entirely invalid descriptors, and the extents
        say where the zeros were."""
        self.build_guest_tables()
        captured = regimes.capture(self.provider(), self.symbols)
        copied = sum(size for _, size in captured["extents"])
        self.assertLess(len(captured["words"]) * 8, copied // 100)

    def test_an_address_the_copy_never_held_is_unreadable(self):
        """Served as zeros it would decode as invalid descriptors, and the
        walk would call an address unmapped when the copy is simply short.
        """
        self.build_guest_tables()
        captured = regimes.capture(self.provider(), self.symbols)
        copy = regimes.Tables.of(captured)
        with self.assertRaises(ValueError):
            copy.read_bytes(RAM_BASE, translation.STAGE2.table_bytes)


class TablesTest(unittest.TestCase):
    """The copy on its own, without an image behind it."""

    PAYLOAD = {
        "regimes": [],
        "extents": [["0x40001000", 4096]],
        "words": {"0x40001000": "0x40002003", "0x40001010": "0xdeadbeef"},
    }

    def setUp(self):
        self.copy = regimes.Tables.of(self.PAYLOAD)

    def test_a_word_that_was_set_comes_back_whole(self):
        # Past 2^53 a JSON number stops being exact, which is why these
        # travel as hex; the value has to survive the round trip.
        self.assertEqual(self.copy.read_bytes(0x4000_1000, 8), (0x4000_2003).to_bytes(8, "little"))

    def test_an_unset_word_reads_as_the_invalid_descriptor_it_was(self):
        self.assertEqual(self.copy.read_bytes(0x4000_1008, 8), bytes(8))

    def test_a_read_crossing_the_end_of_an_extent_fails(self):
        with self.assertRaises(ValueError):
            self.copy.read_bytes(0x4000_1FF8, 16)

    def test_a_read_between_descriptors_fails_rather_than_reading_empty(self):
        with self.assertRaises(ValueError):
            self.copy.read_bytes(0x4000_1004, 8)


if __name__ == "__main__":
    unittest.main()
