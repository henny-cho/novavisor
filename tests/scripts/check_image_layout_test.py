import importlib.util
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "check_image_layout", REPO / "tools/check_image_layout.py"
)
assert SPEC and SPEC.loader
LAYOUT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LAYOUT)


class ImageLayoutTest(unittest.TestCase):
    def test_parses_load_segments(self):
        text = """\
  LOAD 0x010000 0x00000000e0000000 0x00000000e0000000 0x2000 0x3000 R E 0x10000
  LOAD 0x013000 0x00000000e0003000 0x00000000e0003000 0x1000 0x4000 RW  0x10000
"""
        self.assertEqual(
            LAYOUT.parse_program_headers(text),
            [
                LAYOUT.Segment(0xE0000000, 0x3000),
                LAYOUT.Segment(0xE0003000, 0x4000),
            ],
        )

    def test_accepts_bounded_image_with_payload(self):
        errors = LAYOUT.validate(
            entry=0xE0000000,
            segments=[LAYOUT.Segment(0xE0000000, 0x200000)],
            ram_base=0xE0000000,
            ram_size=0x08000000,
            symbols="00000000e0010000 r guest_image_0_start\n",
            require_payload=True,
        )
        self.assertEqual(errors, [])

    def test_rejects_wrong_entry_overflow_and_missing_payload(self):
        errors = LAYOUT.validate(
            entry=0xE0001000,
            segments=[LAYOUT.Segment(0xE7000000, 0x02000000)],
            ram_base=0xE0000000,
            ram_size=0x08000000,
            symbols="",
            require_payload=True,
        )
        self.assertEqual(len(errors), 3)


if __name__ == "__main__":
    unittest.main()
