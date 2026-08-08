"""The page tables a run has, copied once and walked from the copy."""

from __future__ import annotations

import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from novakit.services.workbench import (  # noqa: E402
    hardware,
    observations,  # noqa: E402
    regimes,
    snapshot,
    translation,
)

ELF = REPO / "build" / "aarch64-debug" / "novavisor.elf"
RAM_BASE = hardware.platform()["NOVA_BOARD_PHYS_RAM_BASE"]
S2 = translation.STAGE2_FORMAT


def _ram_size() -> int:
    """As far above the base as any observation reaches."""
    return max(
        obs.pa + snapshot.PAGE_LAYOUTS[obs.layout].size
        for obs in observations.OBSERVATIONS
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
        sets = self.symbols[observations.STAGE2_SETS]
        vttbr = self.symbols[observations.VTTBR]
        l1 = sets.address + self.field(observations.STAGE2_SETS, "l1")
        l2 = sets.address + self.field(observations.STAGE2_SETS, "l2_pool")
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
        sets = self.symbols[observations.STAGE2_SETS].type.element
        el2 = self.symbols[observations.EL2_ROOT].size + self.symbols[observations.EL2_POOL].size
        self.assertEqual(found["vm0.cpu"], sets.size // translation.STAGE2.table_bytes)
        self.assertEqual(found["el2.self"], el2 // translation.STAGE1.table_bytes)

    def test_dma_regimes_come_from_the_contexts_the_smmu_built(self):
        self.build_guest_tables()
        contexts = self.symbols[observations.DMA_CONTEXTS]
        tables = self.symbols[observations.DMA_TABLES]
        root = tables.address + self.field(observations.DMA_TABLES, "l1")
        entry = contexts.address
        self.poke(entry + self.field(observations.DMA_CONTEXTS, "owner_vm"), 0)
        self.poke(entry + self.field(observations.DMA_CONTEXTS, "root_pa"), root)
        self.poke(self.symbols[observations.DMA_CONTEXT_COUNT].address, 1)

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

        from_ram = translation.tree(live, S2, l1, limit=6)
        from_copy = translation.tree(copy, S2, l1, limit=6)
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


class AnswerTest(unittest.TestCase):
    """What a client is handed back, built without an image."""

    GIB = translation.STAGE2.span(0)
    MIB2 = translation.STAGE2.span(1)
    ROOT = 0x4000_0000
    LEAF = 0x4000_1000
    DMA_ROOT = 0x4000_2000
    DMA_LEAF = 0x4000_3000

    def _regime(self, role: str, root: int) -> dict:
        return {
            "id": f"vm0.{role}",
            "label": f"VM 0 · {role.upper()}",
            "role": role,
            "vm": 0,
            "kind": "stage2",
            "root": f"{root:#x}",
            "tables": 6,
        }

    def setUp(self):
        # Two Stage 2 table sets for one VM, overlapping but not equal:
        # the CPU reaches four 2 MiB blocks from zero, the device reaches
        # the first two and one block the CPU has no mapping for.
        block = 0x8000_0000 | 0x7FC | 0b01  # desc::kAttrNormalRwx, a block
        words = {
            f"{self.ROOT:#x}": f"{self.LEAF | 0b11:#x}",
            f"{self.DMA_ROOT:#x}": f"{self.DMA_LEAF | 0b11:#x}",
        }
        for slot in range(4):
            words[f"{self.LEAF + slot * 8:#x}"] = f"{block + slot * self.MIB2:#x}"
        for slot in (0, 1, 8):
            words[f"{self.DMA_LEAF + slot * 8:#x}"] = f"{block + slot * self.MIB2:#x}"
        self.captured = {
            "regimes": [self._regime("cpu", self.ROOT), self._regime("dma", self.DMA_ROOT)],
            "extents": [[f"{self.ROOT:#x}", 16384]],
            "words": words,
        }

    def test_a_row_carries_the_span_it_covers(self):
        """The client must not compute it. Deriving a span needs the
        level shifts, and holding those in the UI is the second copy of
        the encoding this whole path exists to avoid."""
        answer = regimes.answer(self.captured, {"regime": "vm0.cpu"})
        (top,) = answer["tree"]["nodes"]
        (run,) = top["children"]
        self.assertEqual(int(top["size"], 16), self.GIB)
        self.assertEqual((run["count"], int(run["size"], 16)), (4, 4 * self.MIB2))
        self.assertEqual((run["level"], run["kind"]), (2, "block"))

    def test_a_guest_window_is_writable_and_executable_without_complaint(self):
        """Counted, never judged here. Stage 2 grants both on purpose and
        the guest's own Stage 1 splits them; the regime's own control
        register is what says whether the pair is a defect."""
        tree = regimes.answer(self.captured, {"regime": "vm0.cpu"})["tree"]
        self.assertEqual(tree["wx"], 4)
        self.assertFalse(tree["wxn"])

    def test_a_table_row_carries_no_permission(self):
        answer = regimes.answer(self.captured, {"regime": "vm0.cpu"})
        (top,) = answer["tree"]["nodes"]
        self.assertEqual(top["kind"], "table")
        self.assertNotIn("w", top)
        self.assertNotIn("memory", top)

    def test_the_probe_answers_what_may_be_done_there(self):
        """Half of what an address means is the permission the walk ended
        on; sent without it the reader has to go back into the tree."""
        answer = regimes.answer(self.captured, {"regime": "vm0.cpu", "address": "0x201000"})
        probe = answer["probe"]
        self.assertEqual(probe["output"], f"{0x8020_1000:#x}")
        self.assertEqual((probe["level"], probe["fault"]), (2, ""))
        self.assertEqual((probe["w"], probe["x"], probe["memory"]), (True, True, "normal-wb"))
        self.assertEqual([step["index"] for step in probe["steps"]], [0, 1])

    def test_an_unmapped_address_names_the_level_it_stopped_at(self):
        answer = regimes.answer(self.captured, {"regime": "vm0.cpu", "address": "0x1000000"})
        self.assertEqual(answer["probe"]["fault"], "translation")
        self.assertIsNone(answer["probe"]["output"])
        self.assertNotIn("w", answer["probe"])

    def test_an_address_reads_with_or_without_its_prefix(self):
        bare = regimes.answer(self.captured, {"regime": "vm0.cpu", "address": "201000"})
        self.assertEqual(bare["probe"]["output"], f"{0x8020_1000:#x}")

    def test_a_regime_or_an_address_it_cannot_read_is_refused(self):
        with self.assertRaises(KeyError):
            regimes.answer(self.captured, {"regime": "nope"})
        with self.assertRaises(ValueError):
            regimes.answer(self.captured, {"regime": "vm0.cpu", "address": "zzz"})

    def test_the_two_translations_of_one_vm_are_read_by_their_difference(self):
        """They are separate table sets, not one with an overlay, so what
        is worth looking at is where they disagree: a window only the CPU
        reaches is memory no device can touch, and one only DMA reaches is
        a device able to write where the guest cannot look."""
        isolation = regimes.answer(self.captured, {"regime": "vm0.cpu"})["isolation"]
        self.assertEqual((isolation["cpu"], isolation["dma"]), ("vm0.cpu", "vm0.dma"))
        self.assertEqual(isolation["cpu_only"], [[f"{2 * self.MIB2:#x}", f"{2 * self.MIB2:#x}"]])
        self.assertEqual(isolation["dma_only"], [[f"{8 * self.MIB2:#x}", f"{self.MIB2:#x}"]])

    def test_the_difference_reads_the_same_from_either_side(self):
        # It is one fact about the VM, not a property of which chip was
        # picked; naming the sides by role is what makes that true.
        from_cpu = regimes.answer(self.captured, {"regime": "vm0.cpu"})["isolation"]
        from_dma = regimes.answer(self.captured, {"regime": "vm0.dma"})["isolation"]
        self.assertEqual(from_cpu, from_dma)

    def test_a_regime_with_no_counterpart_is_not_compared(self):
        alone = dict(self.captured, regimes=[self._regime("cpu", self.ROOT)])
        self.assertNotIn("isolation", regimes.answer(alone, {"regime": "vm0.cpu"}))

    def test_one_address_is_answered_by_both_translations(self):
        """Asking each separately would let the two answers be about
        different addresses, which is exactly what a comparison cannot
        afford."""
        answer = regimes.answer(
            self.captured, {"regime": "vm0.cpu", "address": f"{3 * self.MIB2:x}"}
        )
        self.assertEqual(answer["probe"]["fault"], "")
        (beside,) = answer["beside"]
        self.assertEqual(beside["regime"], "vm0.dma")
        self.assertEqual(beside["probe"]["address"], answer["probe"]["address"])
        self.assertEqual(beside["probe"]["fault"], "translation")

    def test_every_value_survives_json(self):
        """A descriptor is past 2^53, where a JSON number stops being
        exact. Everything addressed travels as a string for that reason,
        and a round trip is where a missed one shows."""
        answer = regimes.answer(self.captured, {"regime": "vm0.cpu", "address": "0x201000"})
        self.assertEqual(json.loads(json.dumps(answer)), answer)


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
