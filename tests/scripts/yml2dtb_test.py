import importlib.util
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("yml2dtb", REPO / "tools/yml2dtb/yml2dtb.py")
YML2DTB = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(YML2DTB)

BOARD_LAYOUT = REPO / "src/hal/board/qemu_virt/include/board_layout.h"

INVENTORY = """\
sid_bits: 8
devices:
  - id: edu0
    device_id: 0
    compatible: qemu,edu
    streams: [0x10]
    mmio: {base: 0x10000000, size: 0x00100000}
    interrupt: {intid: 37, trigger: level}
    coherent: true
    reset: quiesce
"""

TWO_GUESTS = """\
guests:
  - {name: vm0, memory_size: 0x00100000, vcpus: 1, uart: none}
  - {name: vm1, memory_size: 0x00100000, vcpus: 1, uart: none}
devices:
  - {id: edu0, owner: 1, guest_ipa: 0x10000000, virtual_intid: 48}
"""


class DevicePolicyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.inventory_path = self.root / "inventory.yml"
        self.config_path = self.root / "config.yml"
        self.inventory_path.write_text(INVENTORY)
        self.config_path.write_text(TWO_GUESTS)
        self.layout = YML2DTB.read_layout(YML2DTB.DEFAULT_LAYOUT, BOARD_LAYOUT)

    def tearDown(self):
        self.temp.cleanup()

    def load(self):
        inventory = YML2DTB.load_inventory(self.inventory_path, self.layout)
        guests, assigned = YML2DTB.load_config(self.config_path, self.layout, inventory)
        return inventory, guests, assigned

    def test_emits_runtime_policy_and_owner_only_dtb_node(self):
        inventory, guests, assigned = self.load()
        self.assertEqual(guests[0]["devices"], [])
        self.assertEqual(len(guests[1]["devices"]), 1)
        self.assertNotIn(b"qemu,edu\0", YML2DTB.build_guest_dtb(guests[0], self.layout))
        owner_blob = YML2DTB.build_guest_dtb(guests[1], self.layout)
        self.assertIn(b"qemu,edu\0", owner_blob)
        self.assertIn(b"nova,dma-window\0", owner_blob)

        policy = YML2DTB.emit_device_policy(inventory, assigned)
        self.assertIn(".stream_id = 0x10U, .vm = 1", policy)
        self.assertIn(".ipa_base = 0x10000000ULL", policy)
        self.assertIn(".virtual_intid = 48", policy)

    def test_rejects_invalid_owner_and_missing_stream(self):
        self.config_path.write_text(TWO_GUESTS.replace("owner: 1", "owner: 2"))
        with self.assertRaisesRegex(SystemExit, "outside the guest table"):
            self.load()

        self.inventory_path.write_text(INVENTORY.replace("streams: [0x10]", "streams: []"))
        self.config_path.write_text(TWO_GUESTS)
        with self.assertRaisesRegex(SystemExit, "at least one SID"):
            self.load()

    def test_rejects_physical_mmio_and_irq_collisions(self):
        second = """\
  - id: edu1
    device_id: 1
    compatible: qemu,edu
    streams: [0x11]
    mmio: {base: 0x10000000, size: 0x00100000}
    interrupt: {intid: 38, trigger: edge}
    coherent: false
    reset: function
"""
        self.inventory_path.write_text(INVENTORY + second)
        with self.assertRaisesRegex(SystemExit, "mmio overlaps"):
            self.load()

        second = second.replace("base: 0x10000000", "base: 0x11000000")
        second = second.replace("intid: 38", "intid: 37")
        self.inventory_path.write_text(INVENTORY + second)
        with self.assertRaisesRegex(SystemExit, "intid is invalid or duplicated"):
            self.load()

    def test_rejects_protected_and_guest_visible_overlaps(self):
        self.inventory_path.write_text(INVENTORY.replace("base: 0x10000000", "base: 0x40000000"))
        with self.assertRaisesRegex(SystemExit, "protected EL2 RAM"):
            self.load()

        self.inventory_path.write_text(INVENTORY)
        self.config_path.write_text(TWO_GUESTS.replace("guest_ipa: 0x10000000", "guest_ipa: 0x50000000"))
        with self.assertRaisesRegex(SystemExit, "guest_ipa overlaps guest RAM"):
            self.load()

        self.config_path.write_text(TWO_GUESTS.replace("guest_ipa: 0x10000000", "guest_ipa: 0x60000000"))
        with self.assertRaisesRegex(SystemExit, "guest_ipa overlaps IVC"):
            self.load()


if __name__ == "__main__":
    unittest.main()
