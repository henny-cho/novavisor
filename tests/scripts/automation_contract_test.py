"""Public automation contracts that must survive the CLI migration."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
NOVA = REPO / "scripts" / "nova"
PRESETS = REPO / "CMakePresets.json"
sys.path.insert(0, str(REPO / "scripts"))

from nova_cli import config  # noqa: E402


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )


class PublicCommandContractTest(unittest.TestCase):
    def test_nova_help_exposes_every_top_level_command(self):
        result = run(str(NOVA), "--help")

        self.assertEqual(result.returncode, 0, result.stderr)
        for command in (
            "build",
            "clean",
            "fmt",
            "lint",
            "run",
            "size",
            "objdump",
            "test",
            "demo",
            "firmware",
            "ci",
        ):
            self.assertIn(command, result.stdout)

    def test_demo_help_exposes_every_public_operation(self):
        result = run(str(NOVA), "demo", "--help")

        self.assertEqual(result.returncode, 0, result.stderr)
        for command in (
            "list",
            "build",
            "fetch",
            "run",
            "verify",
            "soak",
            "verify-all",
        ):
            self.assertIn(command, result.stdout)

    def test_debug_is_an_option_on_run_commands(self):
        for command in ((str(NOVA), "run", "--help"), (str(NOVA), "demo", "run", "1", "--help")):
            result = run(*command)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--debug", result.stdout)

    def test_firmware_help_exposes_role_based_operations(self):
        result = run(str(NOVA), "firmware", "--help")

        self.assertEqual(result.returncode, 0, result.stderr)
        for operation in ("profile", "package", "verify"):
            self.assertIn(operation, result.stdout)

    def test_qemu_board_arguments_have_one_internal_owner(self):
        self.assertEqual(
            list(config.QEMU_BOARD_ARGS),
            [
                "-machine",
                "virt,virtualization=on,gic-version=3,iommu=smmuv3,highmem-ecam=off",
                "-cpu",
                "cortex-a57",
                "-smp",
                "2",
                "-nographic",
                "-nic",
                "none",
                "-m",
                "1024",
            ],
        )

    def test_removed_compatibility_commands_are_rejected(self):
        commands = (
            ("debug",),
            ("format",),
            ("demo", "debug", "1"),
            ("demo", "verify" + "-repeat", "1", "--runs", "1"),
            ("demo", "qemu-args"),
            ("firmware", "smoke"),
            ("firmware", "qemu-smoke"),
            ("firmware", "fip"),
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(run(str(NOVA), *command).returncode, 2)

    def test_compatibility_entrypoints_are_removed(self):
        for name in (
            "task" + ".sh",
            "task" + "_legacy.sh",
            "demo" + "_runner.py",
            "setup" + "_env.sh",
            "qemu" + "_tfa_smoke.sh",
            "n1sdp" + "_firmware.sh",
        ):
            self.assertFalse((REPO / "scripts" / name).exists(), name)


class BuildPresetContractTest(unittest.TestCase):
    def test_every_ci_profile_has_matching_configure_and_build_presets(self):
        data = json.loads(PRESETS.read_text())
        configure = {preset["name"] for preset in data["configurePresets"]}
        build = {preset["name"] for preset in data["buildPresets"]}

        required = {
            "host-debug",
            "aarch64-debug",
            "aarch64-release",
            "aarch64-minimal-release",
            "aarch64-standard-release",
            "aarch64-qemu-tfa-release",
            "aarch64-n1sdp-release",
        }
        self.assertLessEqual(required, configure)
        self.assertLessEqual(required, build)

    def test_public_entrypoints_are_executable(self):
        self.assertTrue(NOVA.stat().st_mode & 0o111)


if __name__ == "__main__":
    unittest.main()
