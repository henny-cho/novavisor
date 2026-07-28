import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "payload_manifest", REPO / "tools/payload_manifest.py"
)
assert SPEC and SPEC.loader
PAYLOAD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PAYLOAD)


class PayloadManifestTest(unittest.TestCase):
    def test_pins_digest_and_placement(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "guest.bin"
            binary.write_bytes(b"guest")

            record = PAYLOAD.make_record(
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
                PAYLOAD.make_record(
                    binary,
                    guest=0,
                    name="empty",
                    load_pa=0,
                    entry=0,
                    memory_size=1,
                )

            binary.write_bytes(b"x")
            with self.assertRaisesRegex(ValueError, "non-negative"):
                PAYLOAD.make_record(
                    binary,
                    guest=-1,
                    name="invalid",
                    load_pa=0,
                    entry=0,
                    memory_size=1,
                )


if __name__ == "__main__":
    unittest.main()
