import importlib.util
import hashlib
import tempfile
import unittest
import zlib
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
        self.assertIn("ResetCapability::kQuiesce", policy)
        self.assertIn(".coherent = true", policy)

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

    def test_rejects_inventory_beyond_runtime_capacity(self):
        records = []
        for index in range(YML2DTB.MAX_DEVICES + 1):
            records.append(f"""\
  - id: edu{index}
    device_id: {index}
    compatible: qemu,edu
    streams: [{0x10 + index}]
    mmio: {{base: {0x10000000 + index * 0x100000:#x}, size: 0x00100000}}
    interrupt: {{intid: {40 + index}, trigger: level}}
    coherent: true
    reset: quiesce
""")
        self.inventory_path.write_text("sid_bits: 8\ndevices:\n" + "".join(records))
        with self.assertRaisesRegex(SystemExit, "exceeds registry capacity 8"):
            self.load()


class PayloadBundleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.inventory_path = self.root / "inventory.yml"
        self.config_path = self.root / "config.yml"
        self.binary_path = self.root / "guest.bin"
        self.payload_path = self.root / "payloads.yml"
        self.inventory_path.write_text(INVENTORY)
        self.config_path.write_text(TWO_GUESTS)
        self.binary_path.write_bytes(b"embedded guest image")
        self.layout = YML2DTB.read_layout(YML2DTB.DEFAULT_LAYOUT, BOARD_LAYOUT)
        inventory = YML2DTB.load_inventory(self.inventory_path, self.layout)
        self.guests, _ = YML2DTB.load_config(
            self.config_path, self.layout, inventory
        )

    def tearDown(self):
        self.temp.cleanup()

    def write_payloads(self, *, sha256: str | None = None):
        digest = sha256 or hashlib.sha256(self.binary_path.read_bytes()).hexdigest()
        self.payload_path.write_text(
            "payloads:\n"
            f"  - {{guest: 0, name: vm0, binary: {self.binary_path}, "
            f"sha256: {digest}, load_pa: 0x50000000, "
            "entry: 0x50000000, memory_size: 0x00100000}\n"
            f"  - {{guest: 1, name: vm1, binary: {self.binary_path}, "
            f"sha256: {digest}, load_pa: 0x50200000, "
            "entry: 0x50000000, memory_size: 0x00100000}\n"
        )

    def test_emits_binary_dtb_and_metadata_records(self):
        self.write_payloads()
        payloads = YML2DTB.load_payloads(
            self.payload_path, self.guests, self.layout
        )

        self.assertEqual(payloads[0]["size"], len(self.binary_path.read_bytes()))
        self.assertEqual(
            payloads[0]["checksum"],
            zlib.crc32(self.binary_path.read_bytes()) & 0xFFFFFFFF,
        )
        assembly = YML2DTB.emit_asm(
            self.root, self.guests, payloads, "test-digest"
        )
        self.assertIn(f'.incbin "{self.binary_path}"', assembly)
        self.assertIn(".global g_guest_payloads", assembly)
        self.assertIn(".quad 0x50200000", assembly)
        self.assertIn(f".word 0x{payloads[0]['checksum']:08X}", assembly)

    def test_empty_payload_list_keeps_loader_compatibility(self):
        self.payload_path.write_text("payloads: []\n")
        payloads = YML2DTB.load_payloads(
            self.payload_path, self.guests, self.layout
        )
        assembly = YML2DTB.emit_asm(
            self.root, self.guests, payloads, "test-digest"
        )

        self.assertEqual(payloads, [None, None])
        self.assertNotIn("guest_image_0_start", assembly)
        self.assertIn(".quad 0x0", assembly)
        self.assertIn(".word 0x00000000", assembly)

    def test_rejects_checksum_and_guest_index_mismatch(self):
        self.write_payloads(sha256="0" * 64)
        with self.assertRaisesRegex(SystemExit, "sha256 does not match"):
            YML2DTB.load_payloads(self.payload_path, self.guests, self.layout)

        self.payload_path.write_text("payloads:\n  - {guest: 2}\n")
        with self.assertRaisesRegex(SystemExit, "outside the guest table"):
            YML2DTB.load_payloads(self.payload_path, self.guests, self.layout)

    def test_rejects_binary_overlapping_dtb_reservation(self):
        self.binary_path.write_bytes(
            b"x" * (self.guests[0]["memory_size"] - self.layout["NOVA_GUEST_DTB_SIZE"] + 1)
        )
        self.write_payloads()
        with self.assertRaisesRegex(SystemExit, "overlaps the guest DTB"):
            YML2DTB.load_payloads(self.payload_path, self.guests, self.layout)


if __name__ == "__main__":
    unittest.main()
