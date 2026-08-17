import unittest

from novakit.image import layout


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
