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


if __name__ == "__main__":
    unittest.main()
