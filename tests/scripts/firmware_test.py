"""Firmware packaging and QEMU handoff contracts."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from nova_cli import firmware, qemu  # noqa: E402


class QemuCommandTests(unittest.TestCase):
    def test_secure_firmware_boot_reuses_the_canonical_board(self):
        command = qemu.board_command(
            bios=Path("/tmp/flash.bin"),
            secure=True,
        )

        self.assertEqual(command[0], "qemu-system-aarch64")
        self.assertIn("secure=on", command[2])
        self.assertNotIn("-kernel", command)
        self.assertEqual(command[-2:], ["-bios", "/tmp/flash.bin"])


class FirmwareSourceTests(unittest.TestCase):
    def test_pinned_cached_checkout_avoids_network_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "tf-a"
            (source / ".git").mkdir(parents=True)
            with (
                mock.patch.object(firmware, "tfa_source_dir", return_value=source),
                mock.patch.object(
                    firmware.config,
                    "tool_version",
                    return_value="pinned-commit",
                ),
                mock.patch.object(
                    firmware,
                    "_revision",
                    return_value="pinned-commit",
                ),
                mock.patch.object(firmware.process, "run") as run,
            ):
                resolved = firmware.prepare_tfa_source()

            self.assertEqual(resolved, source)
            run.assert_not_called()


class FirmwarePackagingTests(unittest.TestCase):
    def test_qemu_flash_places_fip_at_the_tfa_offset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = root / "novavisor.bin"
            payload.write_bytes(b"payload")
            tfa_output = root / "tfa"
            tfa_output.mkdir()
            (tfa_output / "bl1.bin").write_bytes(b"BL1")
            (tfa_output / "fip.bin").write_bytes(b"FIP")

            with (
                mock.patch.object(
                    firmware,
                    "_require_payload",
                    return_value=payload,
                ),
                mock.patch.object(
                    firmware,
                    "_build_tfa",
                    return_value=tfa_output,
                ),
            ):
                flash = firmware.package_qemu(payload, root / "output")

            image = flash.read_bytes()
            self.assertEqual(image[:3], b"BL1")
            self.assertEqual(image[256 * 1024 :], b"FIP")


if __name__ == "__main__":
    unittest.main()
