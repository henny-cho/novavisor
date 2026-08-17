"""Firmware packaging and QEMU handoff contracts."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from novakit.core import board
from novakit.services import tfa


class QemuCommandTests(unittest.TestCase):
    def test_secure_firmware_boot_turns_on_the_secure_world(self):
        # The rest of the command is the frozen board model, asserted
        # where that model is owned. Secure world is the one thing only
        # a firmware chain asks for, and BL1 will not run without it.
        command = board.command(bios=Path("/tmp/flash.bin"), secure=True)

        self.assertIn("secure=on", command[2])


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
                    tfa,
                    "_require_payload",
                    return_value=payload,
                ),
                mock.patch.object(
                    tfa,
                    "_build_tfa",
                    return_value=tfa_output,
                ),
            ):
                flash = tfa.package_qemu(payload, root / "output")

            image = flash.read_bytes()
            self.assertEqual(image[:3], b"BL1")
            self.assertEqual(image[tfa.FIP_FLASH_OFFSET :], b"FIP")
