"""Public automation contracts that must survive the CLI migration."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

from typer.testing import CliRunner

REPO = Path(__file__).resolve().parents[2]
NOVA = REPO / "scripts" / "nova"
PRESETS = REPO / "CMakePresets.json"
sys.path.insert(0, str(REPO / "scripts"))

from novakit import cli  # noqa: E402
from novakit.core import board  # noqa: E402


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )


RUNNER = CliRunner()


def listed(output: str, command: str) -> bool:
    return (
        re.search(
            rf"(?m)^[^A-Za-z0-9_\n]*{re.escape(command)}\s",
            output,
        )
        is not None
    )


class PublicCommandContractTest(unittest.TestCase):
    def test_every_canonical_leaf_has_valid_typer_help(self):
        commands = (
            ("build",),
            ("run",),
            ("clean",),
            ("inspect", "size"),
            ("inspect", "disassemble"),
            ("format",),
            ("lint",),
            ("test",),
            ("demo", "list"),
            ("demo", "build"),
            ("demo", "fetch", "--all"),
            ("demo", "run", "1"),
            ("demo", "verify", "--all"),
            ("demo", "soak", "1", "--runs", "1"),
            ("firmware", "build", "n1sdp"),
            ("firmware", "package", "n1sdp", "--payload", "payload.bin"),
            ("firmware", "verify", "qemu-tfa"),
            ("workbench", "serve"),
            ("ci", "host"),
        )
        for command in commands:
            with self.subTest(command=command):
                result = RUNNER.invoke(cli.app, [*command, "--help"], color=False)
                self.assertEqual(result.exit_code, 0, result.output)

    def test_nova_help_exposes_every_top_level_command(self):
        result = run(str(NOVA), "--help")

        self.assertEqual(result.returncode, 0, result.stderr)
        for command in (
            "build",
            "clean",
            "format",
            "inspect",
            "lint",
            "run",
            "test",
            "demo",
            "firmware",
            "workbench",
            "ci",
        ):
            self.assertIn(command, result.stdout)

        for legacy in ("fmt", "size", "objdump"):
            self.assertFalse(listed(result.stdout, legacy))

    def test_inspect_help_groups_image_queries(self):
        result = run(str(NOVA), "inspect", "--help")

        self.assertEqual(result.returncode, 0, result.stderr)
        for operation in ("size", "disassemble"):
            self.assertTrue(listed(result.stdout, operation))

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
        ):
            self.assertTrue(listed(result.stdout, command))
        self.assertFalse(listed(result.stdout, "verify-all"))

    def test_debug_is_an_option_on_run_commands(self):
        for command in ((str(NOVA), "run", "--help"), (str(NOVA), "demo", "run", "1", "--help")):
            result = run(*command)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--debug", result.stdout)

    def test_firmware_help_exposes_role_based_operations(self):
        result = run(str(NOVA), "firmware", "--help")

        self.assertEqual(result.returncode, 0, result.stderr)
        for operation in ("build", "package", "verify"):
            self.assertTrue(listed(result.stdout, operation))
        self.assertFalse(listed(result.stdout, "profile"))

    def test_legacy_command_paths_remain_hidden_compatibility_aliases(self):
        aliases = (
            ("fmt",),
            ("size",),
            ("objdump",),
            ("demo", "verify-all"),
            ("firmware", "profile"),
        )
        for alias in aliases:
            with self.subTest(alias=alias):
                result = run(str(NOVA), *alias, "--help")
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_demo_set_operations_require_one_scope(self):
        for operation in ("fetch", "verify"):
            with self.subTest(operation=operation, scope="missing"):
                self.assertEqual(run(str(NOVA), "demo", operation).returncode, 2)
            with self.subTest(operation=operation, scope="conflicting"):
                result = run(str(NOVA), "demo", operation, "1", "--all")
                self.assertEqual(result.returncode, 2)

    def test_qemu_board_arguments_have_one_internal_owner(self):
        self.assertEqual(
            list(board.MACHINE_ARGS),
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
