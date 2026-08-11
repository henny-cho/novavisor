"""The regimes a run has: a roster read every poll, a copy read once."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import struct
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import workbench_image  # noqa: E402
from novakit.image import elfsym, observe  # noqa: E402
from novakit.services.workbench import (  # noqa: E402
    hardware,
    observations,  # noqa: E402
    regimes,
    snapshot,
    translation,
)

ELF = workbench_image.ELF
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
@unittest.skipUnless(importlib.util.find_spec("elftools"), "pyelftools is not installed")
class CaptureTest(unittest.TestCase):
    """Against the real image: the field offsets are the DWARF's."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.ram_path = Path(self.directory.name) / "guest-ram"
        self.view = workbench_image.view()
        # Written where they are poked and nowhere else. The aperture is
        # half a gigabyte and a test sets a handful of descriptors in it,
        # so the backend is a sparse file rather than a buffer of that
        # size allocated, filled and copied for every test.
        self.words: dict[int, int] = {}
        self.symbols = self.view.walk

    def poke(self, pa: int, *words: int) -> None:
        for index, word in enumerate(words):
            self.words[pa + index * 8] = word

    def provider(self):
        with self.ram_path.open("wb") as ram:
            ram.truncate(_ram_size())
            for pa, word in self.words.items():
                ram.seek(pa - RAM_BASE)
                ram.write(struct.pack("<Q", word))
        made = snapshot.ElfRamProvider(ELF, self.ram_path, RAM_BASE, self.view)
        self.addCleanup(made.close)
        return made

    def capture(self, reader) -> dict:
        """This run's topology, assembled where the bridge assembles it."""
        return regimes.Capture(reader, self.symbols).refresh()

    def field(self, symbol: str, name: str) -> int:
        entry = self.symbols[symbol]
        info = entry.type.element if entry.type.kind == "array" else entry.type
        return next(member.offset for member in info.fields if member.name == name)

    def build_guest_tables(self) -> tuple[int, int]:
        """One guest window: an L1 table entry over four 2 MiB blocks."""
        sets = self.symbols[observe.STAGE2_SETS]
        vttbr = self.symbols[observe.VTTBR]
        l1 = sets.address + self.field(observe.STAGE2_SETS, "l1")
        l2 = sets.address + self.field(observe.STAGE2_SETS, "l2_pool")
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
        self.assertEqual(regimes.roster(self.provider(), self.symbols), [])
        self.assertIsNone(regimes.Capture(self.provider(), self.symbols).refresh())

    def test_the_copy_is_read_once_and_the_roster_at_every_look(self):
        """The two halves move at rates three orders apart: the roster is
        a hundredth of a millisecond, the copy is thirteen of them. Read
        together they would cost the dear one on every poll."""
        self.build_guest_tables()
        capture = regimes.Capture(self.provider(), self.symbols)
        with mock.patch.object(regimes, "copy_tables", wraps=regimes.copy_tables) as copied:
            self.assertIsNotNone(capture.refresh())
            # Nothing moved, so there is nothing to republish -- and the
            # tables were not read a second time to find that out.
            self.assertIsNone(capture.refresh())
            self.assertEqual(copied.call_count, 1)

    def test_a_built_guest_becomes_a_regime_rooted_where_vttbr_points(self):
        l1, _ = self.build_guest_tables()
        captured = self.capture(self.provider())
        cpu = next(entry for entry in captured["regimes"] if entry["id"] == "vm0.cpu")
        self.assertEqual(int(cpu["root"], 16), l1)
        self.assertEqual((cpu["kind"], cpu["vm"], cpu["role"]), ("stage2", 0, "cpu"))

    def test_the_walk_budget_is_the_pool_the_tables_were_built_from(self):
        self.build_guest_tables()
        captured = self.capture(self.provider())
        found = {entry["id"]: entry["tables"] for entry in captured["regimes"]}
        sets = self.symbols[observe.STAGE2_SETS].type.element
        el2 = self.symbols[observe.EL2_ROOT].size + self.symbols[observe.EL2_POOL].size
        self.assertEqual(found["vm0.cpu"], sets.size // translation.STAGE2.table_bytes)
        self.assertEqual(found["el2.self"], el2 // translation.STAGE1.table_bytes)

    def test_dma_regimes_come_from_the_contexts_the_smmu_built(self):
        self.build_guest_tables()
        contexts = self.symbols[observe.DMA_CONTEXTS]
        tables = self.symbols[observe.DMA_TABLES]
        root = tables.address + self.field(observe.DMA_TABLES, "l1")
        entry = contexts.address
        self.poke(entry + self.field(observe.DMA_CONTEXTS, "owner_vm"), 0)
        self.poke(entry + self.field(observe.DMA_CONTEXTS, "root_pa"), root)
        self.poke(self.symbols[observe.DMA_CONTEXT_COUNT].address, 1)

        captured = self.capture(self.provider())
        dma = next(entry for entry in captured["regimes"] if entry["id"] == "vm0.dma")
        self.assertEqual(int(dma["root"], 16), root)
        # Two stage-2 translations of one VM, side by side rather than one
        # over the other: the difference between them is the isolation.
        cpu = next(entry for entry in captured["regimes"] if entry["id"] == "vm0.cpu")
        self.assertNotEqual(dma["root"], cpu["root"])

    def test_a_run_with_no_smmu_has_no_dma_regimes(self):
        self.build_guest_tables()
        captured = self.capture(self.provider())
        self.assertEqual([entry for entry in captured["regimes"] if entry["role"] == "dma"], [])

    def test_the_copy_answers_exactly_what_ram_did(self):
        """The claim the capture rests on: a copy that answered
        differently would make a replay a second program."""
        l1, _ = self.build_guest_tables()
        live = self.provider()
        captured = self.capture(live)
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
        captured = self.capture(self.provider())
        copied = sum(size for _, size in captured["extents"])
        self.assertLess(len(captured["words"]) * 8, copied // 100)

    def test_an_address_the_copy_never_held_is_unreadable(self):
        """Served as zeros it would decode as invalid descriptors, and
        the walk would call an address unmapped when the copy is short.
        """
        self.build_guest_tables()
        captured = self.capture(self.provider())
        copy = regimes.Tables.of(captured)
        with self.assertRaises(ValueError):
            copy.read_bytes(RAM_BASE, translation.STAGE2.table_bytes)


class Published:
    """A copy standing in for live memory, with banks published over it.

    A walk reads both from one reader now — the tables it follows and
    the bank it is rooted in — so a stand-in has to offer both. Each
    look takes the next bank listed and the last one repeats, which is
    how a root that moves under a walk is put in front of one. Tears are
    dealt out on demand, for the wait that a publisher's window needs.
    """

    period_us = round(1_000_000 / observations.PUBLISH_HZ)

    def __init__(
        self,
        tables,
        *looks: list[dict],
        stamp: int | None = None,
        tears: int = 0,
        hold: threading.Event | None = None,
    ):
        self._tables = tables
        self._looks = looks
        self._taken = 0
        self._stamp = stamp
        self.tears = tears
        # A reader stopped mid-walk, for putting a restart in the middle
        # of one. `reading` says it has arrived; `hold` lets it out.
        self._hold = hold
        self.reading = threading.Event()

    def read_bytes(self, pa: int, size: int) -> bytes:
        return self._tables.read_bytes(pa, size)

    def read(self, obs, *, live: bool = True, since: int | None = None):
        del live, since
        self.reading.set()
        if self._hold is not None:
            self._hold.wait(timeout=5)
        if self.tears:
            self.tears -= 1
            raise elfsym.TornRead(f"{obs.topic}: the publisher is inside the window")
        banks = self._looks[min(self._taken, len(self._looks) - 1)]
        self._taken += 1
        return snapshot.Reading(banks, stamp=self._stamp)


class Walkable:
    """One VM's two Stage 2 translations and one guest's own, as a
    topology a walk can be asked about.

    A mixin rather than a base test case: the same fixture answers what
    the walk returns and what the bridge does with it, and two copies of
    a page table geometry would be two chances to describe different
    machines.
    """

    GIB = translation.STAGE2.span(0)
    MIB2 = translation.STAGE2.span(1)
    ROOT = 0x4000_0000
    LEAF = 0x4000_1000
    DMA_ROOT = 0x4000_2000
    DMA_LEAF = 0x4000_3000
    # A guest's own Stage 1 tables. They live where the CPU's Stage 2
    # already maps the guest's first block — IPA 0 onto PA 0x80000000 —
    # so a walk that skipped the second translation would read the wrong
    # gigabyte and no mapping has to be added for them.
    GUEST_BLOCK = 0x8000_0000
    GUEST_L1 = 0x8010_0000
    GUEST_L2 = 0x8010_1000
    GUEST_L3 = 0x8010_2000

    def _regime(self, role: str, root: int) -> dict:
        return {
            "id": f"vm0.{role}",
            "label": f"VM 0 · {role.upper()}",
            "role": role,
            "vm": 0,
            "kind": "stage2",
            "root": f"{root:#x}",
            "tables": 6,
            "ground": regimes.CAPTURED,
            "space": "vm0.ipa",
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
        # The guest's three levels, pointing at each other by IPA.
        words[f"{self.GUEST_L1:#x}"] = f"{self.ipa_of(self.GUEST_L2) | 0b11:#x}"
        words[f"{self.GUEST_L2:#x}"] = f"{self.ipa_of(self.GUEST_L3) | 0b11:#x}"
        words[f"{self.GUEST_L3 + 8:#x}"] = f"{0x0040_0000 | 0x7C0 | 0b11:#x}"  # VA 0x1000 -> IPA 0x400000
        self.captured = {
            "regimes": [self._regime("cpu", self.ROOT), self._regime("dma", self.DMA_ROOT)],
            "extents": [[f"{self.ROOT:#x}", 16384], [f"{self.GUEST_BLOCK:#x}", self.MIB2]],
            "words": words,
        }

    @classmethod
    def ipa_of(cls, pa: int) -> int:
        """The IPA the CPU's Stage 2 maps onto this address."""
        return pa - cls.GUEST_BLOCK

    def _guest(self, high: bool) -> dict:
        return {
            "id": f"vm0.v0.el1.{'high' if high else 'low'}",
            "label": "VM 0 · vCPU 0 · EL1",
            "role": f"el1.{'high' if high else 'low'}",
            "vm": 0,
            "vcpu": 0,
            "kind": f"guest-stage1-{'high' if high else 'low'}",
            "tables": 0,
            "ground": regimes.LIVE,
            "space": "vm0.v0.va",
        }

    # A bank as the S layer publishes one: hex words, MMU on, 4K over 32
    # bits so the fake memory can hold the tables.
    @property
    def BANK(self) -> list[dict]:  # noqa: N802 — a published bank, spelled as the wire does
        return [
            {
                "el1": {
                    "sctlr": "0x1",  # MMU on
                    "tcr": "0x803520",  # 4K over 32 bits, high half disabled
                    "ttbr0": f"{self.ipa_of(self.GUEST_L1):#x}",
                    "ttbr1": "0x0",
                    "mair": "0x0",
                }
            }
        ]

    def topology(self, *, high: bool = False) -> dict:
        """The captured tables plus one of the guest's halves, listed.

        The low half is the one this guest has enabled; the high one is
        listed to ask what happens when a regime exists on the topology
        and the register behind it says the guest turned it off.
        """
        return self.captured | {"regimes": [*self.captured["regimes"], self._guest(high=high)]}

    def live(self, *looks: list[dict], **held) -> Published:
        """This VM's memory and the banks rooting it, from one reader."""
        return Published(regimes.Tables.of(self.captured), *(looks or (self.BANK,)), **held)


class AnswerTest(Walkable, unittest.TestCase):
    """What a client is handed back, built without an image."""

    def test_a_live_regime_is_rooted_when_the_walk_is_asked_for(self):
        """Its root is TTBR_EL1, which follows whatever process the vCPU
        is running. On the topology it would be a value that was true
        once, and the topology would be republished every time a guest
        switched process."""
        topology = self.topology()
        self.assertNotIn("root", self._guest(high=False))
        answer = regimes.answer(topology, {"regime": "vm0.v0.el1.low"}, live=self.live())
        self.assertEqual(answer["root"], f"{self.ipa_of(self.GUEST_L1):#x}")
        self.assertEqual(answer["ground"], regimes.LIVE)

    def test_a_live_regime_carries_no_map(self):
        # Thousands of tables, and the question is about one address.
        topology = self.topology()
        answer = regimes.answer(topology, {"regime": "vm0.v0.el1.low"}, live=self.live())
        self.assertNotIn("tree", answer)
        self.assertNotIn("isolation", answer)

    def test_a_replay_cannot_root_a_live_regime(self):
        """Where a guest is rooted is a register the firmware shadows and
        publishes, read at the moment a walk asks. A replay has neither
        the publisher nor the memory, and the refusal says which."""
        topology = self.topology()
        with self.assertRaises(ValueError) as caught:
            regimes.answer(topology, {"regime": "vm0.v0.el1.low"})
        self.assertIn("no publisher", str(caught.exception))

    def test_a_torn_bank_is_waited_out_rather_than_refused(self):
        """A tear is a moment: the publisher opens its window on every
        visit and closes it inside the same turn. Refusing at the first
        one would throw away a walk for a reason gone in a period."""
        topology = self.topology()
        answer = regimes.answer(
            topology, {"regime": "vm0.v0.el1.low"}, live=self.live(tears=3)
        )
        self.assertEqual(answer["root"], f"{self.ipa_of(self.GUEST_L1):#x}")

    def test_a_bank_torn_past_a_whole_turn_is_refused(self):
        """The other end of the same rule: waiting is bounded by a turn
        of the publisher's clock, and a copy still torn after one is not
        a moment. The refusal is an observation's limit, not a fault the
        machine answered with."""
        topology = self.topology()
        with self.assertRaises(elfsym.TornRead):
            regimes.answer(
                topology, {"regime": "vm0.v0.el1.low"}, live=self.live(tears=1 << 30)
            )

    def test_a_root_that_moved_between_looks_reads_as_moving(self):
        """What the recheck is for. The guest switched process across the
        two walks, so the second one started somewhere else -- which the
        walk can only see because it reads the root itself."""
        topology = self.topology()
        moved = [{"el1": self.BANK[0]["el1"] | {"ttbr0": f"{self.ipa_of(self.GUEST_L2):#x}"}}]
        answer = regimes.answer(
            topology,
            {"regime": "vm0.v0.el1.low", "address": "0x1000"},
            live=self.live(self.BANK, moved),
        )
        self.assertTrue(answer["moving"])

    def test_a_live_answer_says_how_old_the_root_it_used_was(self):
        """A bank is a shadow of a register, updated at a trap. The
        answer carries the same two things a published shadow does --
        the clock of the copy and the topic that dates it -- so the age
        of a derived answer is read by the rule readings already follow.
        """
        topology = self.topology()
        answer = regimes.answer(
            topology, {"regime": "vm0.v0.el1.low"}, live=self.live(stamp=4242)
        )
        dater = observations.POLICY[observations.EL1_BANKS].as_of
        self.assertEqual(answer["rooted"], {"at": 4242, "as_of": dater, "slot": 0})

    def test_a_root_with_no_publisher_behind_it_carries_no_age(self):
        """A made-up clock cannot be told from a real one, and the UI
        would subtract it."""
        topology = self.topology()
        answer = regimes.answer(topology, {"regime": "vm0.v0.el1.low"}, live=self.live())
        self.assertNotIn("rooted", answer)

    def test_a_captured_answer_has_no_age_to_carry(self):
        """Its root is on the topology because it does not move."""
        self.assertNotIn("rooted", regimes.answer(self.captured, {"regime": "vm0.cpu"}))

    def test_a_half_the_guest_turned_off_cannot_be_walked(self):
        topology = self.topology(high=True)
        with self.assertRaises(ValueError):
            regimes.answer(topology, {"regime": "vm0.v0.el1.high"}, live=self.live())

    def test_a_live_answer_closes_the_chain_and_says_if_it_moved(self):
        """VA to IPA is half an answer: the output is an input to the
        translation beneath, and a reader holding the two halves apart is
        a reader doing the walk. The recheck is reported rather than
        retried — how many tries would settle it is not a known number."""
        topology = self.topology()
        answer = regimes.answer(
            topology, {"regime": "vm0.v0.el1.low", "address": "0x1000"}, live=self.live()
        )
        self.assertIn("moving", answer)
        self.assertFalse(answer["moving"])  # this copy cannot change under the walk
        self.assertEqual(answer["through"]["regime"], "vm0.cpu")
        # The chain: the guest's own walk answered an IPA, and Stage 2
        # answered that IPA rather than the address that was asked.
        self.assertEqual(answer["through"]["probe"]["address"], answer["probe"]["output"])

    def test_a_captured_answer_is_not_rechecked(self):
        # Its ground does not move, so a second walk would be two reads
        # of one fact.
        answer = regimes.answer(self.captured, {"regime": "vm0.cpu", "address": "0x201000"})
        self.assertNotIn("moving", answer)
        self.assertNotIn("through", answer)

    def test_beside_stays_inside_one_address_space(self):
        """The same number in a VA regime and an IPA regime is two
        questions. Answered together it reads as one, and the second
        answer looks like a fact about the address that was asked."""
        topology = self.topology()
        answer = regimes.answer(
            topology, {"regime": "vm0.cpu", "address": "0x201000"}, live=self.live()
        )
        self.assertEqual([beside["regime"] for beside in answer["beside"]], ["vm0.dma"])

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
        """Counted, never judged here: Stage 2 grants both on purpose and
        the regime's control register says whether that is a defect."""
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
        """Separate table sets, not one with an overlay, so the reading
        is where they disagree: a window only the CPU reaches is memory
        no device can touch, one only DMA reaches is a device able to
        write where the guest cannot look."""
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
        different addresses."""
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
        exact; a round trip is where a missed string shows."""
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


class BridgeProbeTest(Walkable, unittest.IsolatedAsyncioTestCase):
    """The walk, reached the way a client reaches it.

    Everything under this seam is checked above. This is the seam
    itself, and nothing ran it: the probe is the one uplink with no
    precondition, and the dispatch test walks only the handlers that
    have one.

    Not restaged here: that a second reader does not starve the first,
    and that a stamp stays with the reading it came from. Those are
    facts about the published region, checked against a real one where a
    stand-in could not fail them.
    """

    def bridge(self, live=None, topology: dict | None = None):
        from novakit.services.workbench.server import Bridge

        bridge = Bridge(ui_root=Path("/nonexistent"))
        if topology is not None:
            bridge.session.adopt_memory_map(topology)
        bridge._provider = live
        bridge.store.drain()
        return bridge

    @staticmethod
    def _ask(bridge, **data) -> None:
        bridge._handle_uplink(
            json.dumps({"topic": "probe", "data": data, "request_id": "probe:1"})
        )

    async def answered(self, bridge, **data) -> dict:
        self._ask(bridge, **data)
        await bridge.settled()
        found = [f["data"] for f in bridge.store.drain() if f["topic"] == "probe"]
        self.assertEqual(len(found), 1, f"expected one answer, got {found}")
        return found[0]

    async def refused(self, bridge, **data) -> str:
        self._ask(bridge, **data)
        await bridge.settled()
        said = [
            f["data"]["reason"]
            for f in bridge.store.drain()
            if f["data"].get("phase") == "uplink-rejected"
        ]
        self.assertEqual(len(said), 1, f"expected one refusal, got {said}")
        self.assertTrue(said[0].startswith("probe: "), said[0])
        return said[0]

    async def test_the_bridge_closes_a_guest_address_to_a_physical_one(self):
        """The whole chain in one answer: the guest's own tables give an
        IPA, and the translation beneath says where that physically is.
        Held apart, the reader would be doing the second walk."""
        bridge = self.bridge(self.live(), self.topology())
        answer = await self.answered(bridge, regime="vm0.v0.el1.low", address="0x1000")
        self.assertEqual(answer["probe"]["output"], "0x400000")
        self.assertEqual(answer["through"]["regime"], "vm0.cpu")
        self.assertEqual(answer["through"]["probe"]["output"], f"{self.GUEST_BLOCK + 0x400000:#x}")

    async def test_the_answer_says_how_old_the_root_it_walked_from_was(self):
        bridge = self.bridge(self.live(stamp=7000), self.topology())
        answer = await self.answered(bridge, regime="vm0.v0.el1.low")
        self.assertEqual(answer["rooted"]["at"], 7000)

    async def test_a_run_with_no_tables_published_is_refused(self):
        reason = await self.refused(self.bridge(self.live()), regime="vm0.cpu")
        self.assertIn("no page tables", reason)

    async def test_a_replay_is_told_which_half_it_is_missing(self):
        """A recording holds the tables EL2 built. It does not hold the
        register a guest's own walk starts from, because that moves."""
        bridge = self.bridge(None, self.topology())
        self.assertIn("no publisher", await self.refused(bridge, regime="vm0.v0.el1.low"))

    async def test_a_half_the_guest_turned_off_is_refused_by_name(self):
        bridge = self.bridge(self.live(), self.topology(high=True))
        reason = await self.refused(bridge, regime="vm0.v0.el1.high")
        self.assertIn("not translating now", reason)

    async def test_a_bank_torn_past_a_whole_turn_is_refused_not_answered(self):
        """An observation's limit, said as one. A fault is the machine's
        answer and travels inside the probe; this is the bridge saying
        it could not take a reading."""
        bridge = self.bridge(self.live(tears=1 << 30), self.topology())
        reason = await self.refused(bridge, regime="vm0.v0.el1.low")
        self.assertIn("publisher is inside the window", reason)

    async def test_an_answer_for_a_run_that_has_ended_is_dropped(self):
        """The map is of addresses that machine held. Published after a
        restart it describes one that no longer exists, and nothing on
        the screen would say so."""
        hold = threading.Event()
        live = self.live(hold=hold)
        bridge = self.bridge(live, self.topology())
        bridge.session.run_id = 1
        self._ask(bridge, regime="vm0.v0.el1.low", address="0x1000")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, live.reading.wait, 5)
        bridge.session.run_id = 2
        hold.set()
        await bridge.settled()
        self.assertEqual([f for f in bridge.store.drain() if f["topic"] == "probe"], [])


if __name__ == "__main__":
    unittest.main()
