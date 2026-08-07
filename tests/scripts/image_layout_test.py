import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from novakit.image import layout  # noqa: E402


class ImageLayoutTest(unittest.TestCase):
    def test_parses_load_segments(self):
        text = """\
  LOAD 0x010000 0x00000000e0000000 0x00000000e0000000 0x2000 0x3000 R E 0x10000
  LOAD 0x013000 0x00000000e0003000 0x00000000e0003000 0x1000 0x4000 RW  0x10000
"""
        self.assertEqual(
            layout.parse_program_headers(text),
            [
                layout.Segment(0xE0000000, 0x3000),
                layout.Segment(0xE0003000, 0x4000),
            ],
        )

    def test_accepts_bounded_image_with_payload(self):
        errors = layout.validate(
            entry=0xE0000000,
            segments=[layout.Segment(0xE0000000, 0x200000)],
            ram_base=0xE0000000,
            ram_size=0x08000000,
            symbols="00000000e0010000 r guest_image_0_start\n",
            require_payload=True,
        )
        self.assertEqual(errors, [])

    def test_rejects_wrong_entry_overflow_and_missing_payload(self):
        errors = layout.validate(
            entry=0xE0001000,
            segments=[layout.Segment(0xE7000000, 0x02000000)],
            ram_base=0xE0000000,
            ram_size=0x08000000,
            symbols="",
            require_payload=True,
        )
        self.assertEqual(len(errors), 3)


if __name__ == "__main__":
    unittest.main()


class TraceRegionTest(unittest.TestCase):
    """The trace rings sit in a gap every board leaves between the IVC
    page and the pristine images.

    The gap is real on all three today, but "real" and "reserved" are
    different things: this holds every board to the reservation, and the
    DTB generator's overlap check holds the rest of the map off it.
    """

    BOARDS = ("qemu_virt", "qemu_tfa", "n1sdp")

    def layout(self, board: str) -> dict[str, int]:
        from novakit.image import abi

        header = (
            REPO / "src" / "hal" / "board" / board
            / "include" / "hal" / "board" / "active" / "board_layout.h"
        )
        values = abi.read_defines(
            header,
            ["NOVA_BOARD_IVC_SHM_PA", "NOVA_BOARD_TRACE_PA", "NOVA_BOARD_PRISTINE_PA"],
        )
        values |= abi.read_defines(abi.GUEST_LAYOUT, ["NOVA_IVC_SHM_SIZE"])
        values |= abi.read_defines(abi.TRACE_RING, ["NOVA_TRACE_SIZE"])
        return values

    def test_every_board_reserves_the_region(self):
        for board in self.BOARDS:
            with self.subTest(board=board):
                self.assertIn("NOVA_BOARD_TRACE_PA", self.layout(board))

    def test_the_region_clears_the_ivc_page_and_the_pristine_images(self):
        for board in self.BOARDS:
            with self.subTest(board=board):
                values = self.layout(board)
                start = values["NOVA_BOARD_TRACE_PA"]
                end = start + values["NOVA_TRACE_SIZE"]
                after_ivc = values["NOVA_BOARD_IVC_SHM_PA"] + values["NOVA_IVC_SHM_SIZE"]
                self.assertGreaterEqual(start, after_ivc)
                self.assertLessEqual(end, values["NOVA_BOARD_PRISTINE_PA"])

    def test_the_region_holds_every_ring_the_writer_will_place(self):
        """Sizing is the header's own arithmetic; if it stops adding up,
        rings would silently overlap each other rather than fail."""
        from novakit.image import abi

        values = abi.read_defines(
            abi.TRACE_RING,
            [
                "NOVA_TRACE_SIZE",
                "NOVA_TRACE_HEADER_SIZE",
                "NOVA_TRACE_RECORDS_OFF",
                "NOVA_TRACE_REC_SIZE",
                "NOVA_TRACE_CAPACITY",
            ],
        )
        cpus = abi.read_defines(
            REPO / "src" / "hal" / "board" / "qemu_virt" / "include"
            / "hal" / "board" / "active" / "board_layout.h",
            ["NOVA_BOARD_SMP_CPUS"],
        )["NOVA_BOARD_SMP_CPUS"]
        stride = (
            values["NOVA_TRACE_RECORDS_OFF"]
            + values["NOVA_TRACE_REC_SIZE"] * values["NOVA_TRACE_CAPACITY"]
        )
        needed = values["NOVA_TRACE_HEADER_SIZE"] + stride * cpus
        self.assertLessEqual(needed, values["NOVA_TRACE_SIZE"])

    def test_a_record_is_a_power_of_two(self):
        """Indexing is a mask, and no record may straddle a cache line."""
        from novakit.image import abi

        size = abi.read_define(abi.TRACE_RING, "NOVA_TRACE_REC_SIZE")
        self.assertEqual(size & (size - 1), 0)
        capacity = abi.read_define(abi.TRACE_RING, "NOVA_TRACE_CAPACITY")
        self.assertEqual(capacity & (capacity - 1), 0)
