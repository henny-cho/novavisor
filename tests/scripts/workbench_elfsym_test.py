"""Pure contracts of the symbol reader: mangling and typed decoding."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from novakit.services.workbench import elfsym  # noqa: E402
from novakit.services.workbench.elfsym import Field, TypeInfo  # noqa: E402


class MangleTest(unittest.TestCase):
    def test_named_namespaces(self):
        self.assertEqual(elfsym.mangle("nova::vcpu::g_sched"), "_ZN4nova4vcpu7g_schedE")
        self.assertEqual(elfsym.mangle("nova::smp::g_mail"), "_ZN4nova3smp6g_mailE")

    def test_anonymous_namespace_marks_internal_linkage(self):
        self.assertEqual(
            elfsym.mangle("nova::vgic::(anonymous)::g_dist"),
            "_ZN4nova4vgic12_GLOBAL__N_1L6g_distE",
        )
        self.assertEqual(
            elfsym.mangle("nova::soft_timer::(anonymous)::g_queue"),
            "_ZN4nova10soft_timer12_GLOBAL__N_1L7g_queueE",
        )

    def test_global_scope_passes_through(self):
        self.assertEqual(elfsym.mangle("g_guest_payloads"), "g_guest_payloads")


UINT64 = TypeInfo("uint", 8)
BOOL = TypeInfo("bool", 1)
STATE = TypeInfo(
    "enum",
    1,
    name="State",
    enumerators=((0, "kOff"), (1, "kReady"), (2, "kRunning"), (3, "kBlocked")),
)
SLOT = TypeInfo(
    "struct",
    24,
    name="Slot",
    fields=(
        Field("deadline", 0, UINT64),
        Field("fn", 8, TypeInfo("pointer", 8)),
        Field("arg", 16, UINT64),
    ),
)


class DecodeTest(unittest.TestCase):
    def test_scalars(self):
        self.assertEqual(elfsym.decode(UINT64, (42).to_bytes(8, "little")), 42)
        self.assertEqual(elfsym.decode(TypeInfo("int", 4), (-3).to_bytes(4, "little", signed=True)), -3)
        self.assertIs(elfsym.decode(BOOL, b"\x01"), True)

    def test_enum_labels_and_torn_reads(self):
        self.assertEqual(elfsym.decode(STATE, b"\x02"), "kRunning")
        with self.assertRaises(elfsym.TornRead):
            elfsym.decode(STATE, b"\x77")

    def test_array_of_structs_with_field_selection(self):
        array = TypeInfo("array", 48, element=SLOT, count=2)
        payload = (
            (100).to_bytes(8, "little") + (0).to_bytes(8, "little") + (7).to_bytes(8, "little")
        ) + (
            (200).to_bytes(8, "little") + (0).to_bytes(8, "little") + (9).to_bytes(8, "little")
        )

        decoded = elfsym.decode(array, payload, fields=("deadline",))

        self.assertEqual(decoded, [{"deadline": 100}, {"deadline": 200}])


if __name__ == "__main__":
    unittest.main()
