"""The board map's contract with the headers it is generated from.

The map exists so no address is ever typed into the UI twice. That only
holds while every value it publishes still traces back to a define, so a
renamed constant, an overlapping region, or a device inventory that has
drifted from the guest ABI must fail here rather than draw a wrong
picture in a browser.
"""

from __future__ import annotations

import json
import unittest

from novakit.image import abi
from novakit.services.workbench import hardware

from tests import REPO

BOARDS = sorted(path.name for path in hardware.BOARD_DIR.iterdir() if path.is_dir())


class BoardMapTest(unittest.TestCase):
    def setUp(self):
        self.board = hardware.DEFAULT_BOARD
        self.map = hardware.board_map(self.board)
        self.header = hardware.board_layout_header(self.board)

    def test_every_board_has_the_headers_the_map_reads(self):
        # A board that ships without them is not drawable, and the bridge
        # would only discover that when someone selected it.
        self.assertIn(hardware.DEFAULT_BOARD, BOARDS)
        for board in BOARDS:
            with self.subTest(board=board):
                self.assertTrue(hardware.board_layout_header(board).is_file())
                self.assertTrue(hardware.inventory_path(board).is_file())
                hardware.board_map(board)  # raises if a define is missing

    def test_block_addresses_come_from_the_header(self):
        blocks = {block["id"]: block for block in self.map["blocks"]}
        expected = {
            "gicd": "NOVA_BOARD_GICD_BASE",
            "gicr0": "NOVA_BOARD_GICR_BASE",
            "smmu": "NOVA_BOARD_SMMU_BASE",
            "uart0": "NOVA_BOARD_UART0_BASE",
            "ecam": "NOVA_BOARD_PCIE_ECAM_BASE",
        }
        values = abi.read_defines(self.header, list(expected.values()))
        for block, define in expected.items():
            with self.subTest(block=block):
                self.assertEqual(blocks[block]["base"], values[define])

    def test_one_redistributor_frame_per_pe(self):
        # The UI places GICR·n under pCPUn by index alone; a frame count
        # that disagrees with the CPU count would mislabel every frame.
        cpus = self.map["cpus"]
        frames = [block for block in self.map["blocks"] if block["id"].startswith("gicr")]
        self.assertEqual(len(frames), cpus)
        stride = abi.read_define(
            REPO / "src" / "nova" / "arch" / "gicv3" / "regs.h", "NOVA_GICR_FRAME_SIZE"
        )
        base = abi.read_define(self.header, "NOVA_BOARD_GICR_BASE")
        for cpu, frame in enumerate(frames):
            with self.subTest(cpu=cpu):
                self.assertEqual(frame["cpu"], cpu)
                self.assertEqual(frame["base"], base + cpu * stride)
                self.assertEqual(frame["size"], stride)

    def test_physical_regions_tile_the_ram_window(self):
        # Sorted, non-overlapping, and gapless: the strip is read as a
        # map of the whole window, so an unnamed gap is a lie about it.
        values = abi.read_defines(
            self.header, ["NOVA_BOARD_PHYS_RAM_BASE", "NOVA_BOARD_PHYS_RAM_SIZE"]
        )
        cursor = values["NOVA_BOARD_PHYS_RAM_BASE"]
        end = cursor + values["NOVA_BOARD_PHYS_RAM_SIZE"]
        for region in self.map["regions"]["pa"]:
            with self.subTest(region=region["name"], base=hex(region["base"])):
                self.assertEqual(region["base"], cursor)
                self.assertGreater(region["size"], 0)
                cursor += region["size"]
        self.assertEqual(cursor, end)

    def test_intermediate_regions_are_ordered_and_disjoint(self):
        cursor = 0
        for region in self.map["regions"]["ipa"]:
            with self.subTest(region=region["name"], base=hex(region["base"])):
                self.assertGreaterEqual(region["base"], cursor)
                self.assertGreater(region["size"], 0)
                cursor = region["base"] + region["size"]

    def test_the_device_inventory_agrees_with_the_guest_abi(self):
        # device_inventory.yml restates the guest-visible EDU window
        # because it is plain generator input and cannot include the
        # header. Until now that agreement was asserted only by a comment.
        inventory = hardware.load_inventory(hardware.inventory_path(self.board))
        edu = next(
            device for device in inventory["devices"] if device["compatible"] == "qemu,edu"
        )
        values = abi.read_defines(
            abi.GUEST_LAYOUT, ["NOVA_EDU_BAR0_IPA", "NOVA_EDU_BAR0_SIZE", "NOVA_EDU_SPI"]
        )
        self.assertEqual(edu["mmio"]["base"], values["NOVA_EDU_BAR0_IPA"])
        self.assertEqual(edu["mmio"]["size"], values["NOVA_EDU_BAR0_SIZE"])
        self.assertEqual(edu["interrupt"]["intid"], values["NOVA_EDU_SPI"])

    def test_the_map_survives_the_wire(self):
        # It travels inside the topo snapshot; a value JSON cannot carry
        # would take the whole snapshot down with it.
        self.assertEqual(json.loads(json.dumps(self.map)), self.map)
