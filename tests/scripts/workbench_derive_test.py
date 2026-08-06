"""Firmware encodings decoded on the bridge."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from novakit.services.workbench import derive, elfsym  # noqa: E402
from novakit.services.workbench.observations import OBSERVATIONS  # noqa: E402

U8 = elfsym.TypeInfo("uint", 1)
U64 = elfsym.TypeInfo("uint", 8)
BOOL = elfsym.TypeInfo("bool", 1)
CPU = elfsym.TypeInfo(
    "struct",
    24,
    name="CpuSched",
    fields=(
        elfsym.Field("current", 0, U64),
        elfsym.Field("fp", 8, U64),
        elfsym.Field("idling", 16, BOOL),
    ),
)


class NoneIfUnsetTest(unittest.TestCase):
    def test_all_bits_set_becomes_null_at_any_width(self):
        # kNoVcpu, kNoOwner, kNoDeadline and kNoResident are each ~0 of
        # their own type, so the width comes from the DWARF and no
        # constant is read twice.
        self.assertIsNone(derive.none_if_unset((1 << 64) - 1, U64))
        self.assertIsNone(derive.none_if_unset(0xFF, U8))

    def test_a_real_value_is_untouched(self):
        self.assertEqual(derive.none_if_unset(0, U64), 0)
        self.assertEqual(derive.none_if_unset((1 << 64) - 2, U64), (1 << 64) - 2)
        # 0xFF is "none" for a byte and an ordinary number for a word.
        self.assertEqual(derive.none_if_unset(0xFF, U64), 0xFF)

    def test_it_walks_arrays_and_structs_and_leaves_other_kinds_alone(self):
        array = elfsym.TypeInfo("array", CPU.size * 2, element=CPU, count=2)
        decoded = [
            {"current": 1, "fp": (1 << 64) - 1, "idling": False},
            {"current": (1 << 64) - 1, "fp": 0, "idling": True},
        ]
        self.assertEqual(
            derive.none_if_unset(decoded, array),
            [
                {"current": 1, "fp": None, "idling": False},
                {"current": None, "fp": 0, "idling": True},
            ],
        )

    def test_it_is_opt_in_per_observation(self):
        # A bitmap with every bit set means "all", not "none". Only the
        # observation knows which it holds, so nothing applies this
        # decoding to a topic that did not ask for it.
        wearing = {obs.topic for obs in OBSERVATIONS if obs.shape is derive.none_if_unset}
        self.assertTrue(wearing)
        for topic in ("sched.affinity", "vgic.dist", "ivc.page"):
            self.assertNotIn(topic, wearing)


class TimerArmedTest(unittest.TestCase):
    """The queue as the deadlines it holds, not as its whole table."""

    QUEUE = elfsym.TypeInfo("array", 0, element=U64, count=22)

    def test_only_armed_slots_travel_and_keep_their_index(self):
        # The owner label is looked up by slot, so dropping the unarmed
        # ones must not renumber the rest.
        queue = [{"deadline": None, "armed": False}] * 22
        queue[0] = {"deadline": 0x1A2B, "armed": True}
        queue[14] = {"deadline": 0x3C4D, "armed": True}
        (cpu,) = derive.timer_armed([queue], self.QUEUE)
        self.assertEqual(cpu, [{"slot": 0, "deadline": 0x1A2B}, {"slot": 14, "deadline": 0x3C4D}])

    def test_an_idle_core_sends_an_empty_list(self):
        self.assertEqual(
            derive.timer_armed([[{"deadline": None, "armed": False}] * 22], self.QUEUE), [[]]
        )

    def test_an_armed_slot_keeps_whatever_deadline_it_holds(self):
        # An armed slot holding kNoDeadline is a firmware fault, and
        # hiding it would be the wrong favour.
        (cpu,) = derive.timer_armed(
            [[{"deadline": (1 << 64) - 1, "armed": True}]], self.QUEUE
        )
        self.assertEqual(cpu, [{"slot": 0, "deadline": (1 << 64) - 1}])


class VgicInflightTest(unittest.TestCase):
    """A list register decoded as the interrupt it is carrying."""

    # Built the way the firmware builds one (make_lr, vgic_delivery.hpp),
    # from the same header, so the test states meaning and not bits.
    @staticmethod
    def lr(vintid, state, priority=0xA0, group1=True, eoi=False):
        raw = state << derive._STATE_SHIFT | vintid
        raw |= priority << derive._LR["NOVA_ICH_LR_PRIORITY_SHIFT"]
        if group1:
            raw |= derive._LR["NOVA_ICH_LR_GROUP1"]
        if eoi:
            raw |= derive._LR["NOVA_ICH_LR_EOI"]
        return raw

    @staticmethod
    def token(vintid, pintid, generation=1):
        return {"virtual_intid": vintid, "physical_intid": pintid, "generation": generation}

    #: An untracked slot: what post_private and post_spi leave behind.
    BARE = {"virtual_intid": 0, "physical_intid": 0, "generation": 0}

    def shadow(self, *rows, tokens=None):
        array = elfsym.TypeInfo("array", 8 * 16, element=U64, count=16)
        cpus = []
        for at, row in enumerate(rows):
            held = (tokens or {}).get(at, {})
            cpus.append({
                "lr": list(row) + [0] * (16 - len(row)),
                "lr_token": [held.get(slot, self.BARE) for slot in range(16)],
            })
        return derive.vgic_inflight(cpus, array)

    def test_only_entries_in_flight_travel(self):
        # The shadow is sized for the architectural 16 while the machine
        # reports four; sending it whole is 128 mostly-zero words.
        (live,) = self.shadow([self.lr(27, 1), 0, self.lr(33, 2), 0])
        self.assertEqual([entry["slot"] for entry in live], [0, 2])
        self.assertEqual([entry["vintid"] for entry in live], [27, 33])

    def test_every_state_encoding_is_named(self):
        (live,) = self.shadow([self.lr(1, 1), self.lr(2, 2), self.lr(3, 3)])
        self.assertEqual(
            [entry["state"] for entry in live], ["pending", "active", "pending+active"]
        )
        self.assertEqual(self.shadow([self.lr(4, 0)]), [[]])  # 00 holds nothing

    def test_the_fields_land_where_the_register_puts_them(self):
        (live,) = self.shadow([self.lr(27, 1, priority=0x80, group1=True, eoi=True)])
        self.assertEqual(
            live[0],
            {
                "slot": 0,
                "vintid": 27,
                "state": "pending",
                "group1": True,
                "prio": 0x80,
                "eoi": True,
            },
        )

    def test_each_vcpu_keeps_its_own_list(self):
        idle, busy = self.shadow([], [self.lr(30, 1)])
        self.assertEqual(idle, [])
        self.assertEqual([entry["vintid"] for entry in busy], [30])

    def test_a_tracked_interrupt_carries_the_silicon_it_came_from(self):
        # The whole point of the passthrough demos: which physical SPI is
        # behind the number the guest sees.
        (live,) = self.shadow([self.lr(37, 1)], tokens={0: {0: self.token(37, 106, 4)}})
        self.assertEqual(live[0]["vintid"], 37)
        self.assertEqual(live[0]["pintid"], 106)
        self.assertEqual(live[0]["generation"], 4)

    def test_an_untracked_interrupt_says_nothing_rather_than_null(self):
        # post_private and post_spi bind no token because there is no
        # physical interrupt behind them. Absent and null would read the
        # same on the wire, and they are not the same fact: one is "the
        # hypervisor made this", the other "we do not know".
        (live,) = self.shadow([self.lr(27, 1)])
        self.assertNotIn("pintid", live[0])
        self.assertNotIn("generation", live[0])

    def test_the_token_is_read_from_the_slot_it_belongs_to(self):
        # Tokens are indexed by list register, not by position in the
        # in-flight list. Reading them in order would attach the wrong
        # physical interrupt to the wrong virtual one.
        (live,) = self.shadow(
            [0, self.lr(37, 1), 0, self.lr(38, 2)],
            tokens={0: {1: self.token(37, 106), 3: self.token(38, 108)}},
        )
        self.assertEqual([(e["vintid"], e["pintid"]) for e in live], [(37, 106), (38, 108)])

    def test_a_shadow_with_no_token_array_still_decodes(self):
        # The manifest asks for both fields, but a decode that hard-fails
        # on a missing one turns a firmware rename into a blank panel
        # instead of a caught error.
        array = elfsym.TypeInfo("array", 8 * 16, element=U64, count=16)
        (live,) = derive.vgic_inflight([{"lr": [self.lr(27, 1)]}], array)
        self.assertEqual(live[0]["vintid"], 27)
        self.assertNotIn("pintid", live[0])


class VgicPostedTest(unittest.TestCase):
    """The hop before a list register: posted, not yet refilled."""

    SPIS = elfsym.TypeInfo("array", 0, element=U64, count=32)

    @staticmethod
    def bank(**bound):
        empty = {"virtual_intid": 0, "physical_intid": 0, "generation": 0}
        return [
            bound.get(f"s{spi}", empty)
            for spi in range(32)
        ]

    def test_only_bound_tokens_travel(self):
        vm0 = self.bank(s5={"virtual_intid": 37, "physical_intid": 106, "generation": 2})
        (posted, idle) = derive.vgic_posted([vm0, self.bank()], self.SPIS)
        self.assertEqual(
            posted, [{"spi": 5, "vintid": 37, "pintid": 106, "generation": 2}]
        )
        self.assertEqual(idle, [])

    def test_an_idle_machine_sends_an_empty_list_per_vm(self):
        self.assertEqual(derive.vgic_posted([self.bank()] * 4, self.SPIS), [[], [], [], []])

    def test_the_spi_index_is_the_bank_position(self):
        # The bank is indexed by SPI number minus the private range, and
        # the position is what lets a reader name the interrupt.
        vm0 = self.bank(s31={"virtual_intid": 63, "physical_intid": 200, "generation": 9})
        (posted, *_) = derive.vgic_posted([vm0], self.SPIS)
        self.assertEqual(posted[0]["spi"], 31)


if __name__ == "__main__":
    unittest.main()
