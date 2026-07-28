import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from novakit.image import payload  # noqa: E402


class PayloadManifestTest(unittest.TestCase):
    def test_pins_digest_and_placement(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "guest.bin"
            binary.write_bytes(b"guest")

            record = payload.make_record(
                binary,
                guest=0,
                name="smoke",
                load_pa=0x80000000,
                entry=0x50000000,
                memory_size=0x100000,
            )

        self.assertEqual(record["binary"], str(binary.resolve()))
        self.assertEqual(record["load_pa"], 0x80000000)
        self.assertEqual(
            record["sha256"], hashlib.sha256(b"guest").hexdigest()
        )

    def test_rejects_empty_binary_and_invalid_placement(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "guest.bin"
            binary.write_bytes(b"")
            with self.assertRaisesRegex(ValueError, "must not be empty"):
                payload.make_record(
                    binary,
                    guest=0,
                    name="empty",
                    load_pa=0,
                    entry=0,
                    memory_size=1,
                )

            binary.write_bytes(b"x")
            with self.assertRaisesRegex(ValueError, "non-negative"):
                payload.make_record(
                    binary,
                    guest=-1,
                    name="invalid",
                    load_pa=0,
                    entry=0,
                    memory_size=1,
                )


if __name__ == "__main__":
    unittest.main()
