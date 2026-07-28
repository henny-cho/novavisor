"""Public automation contracts that must survive the CLI migration."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
TASK = REPO / "scripts" / "task.sh"
DEMO = REPO / "scripts" / "demo_runner.py"
PRESETS = REPO / "CMakePresets.json"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )


class PublicCommandContractTest(unittest.TestCase):
    def test_task_help_exposes_every_top_level_command(self):
        result = run(str(TASK), "--help")

        self.assertEqual(result.returncode, 0, result.stderr)
        for command in (
            "build",
            "clean",
            "format",
            "lint",
            "run",
            "debug",
            "size",
            "objdump",
            "test",
            "demo",
            "firmware",
            "ci",
        ):
            self.assertRegex(result.stdout, rf"(?m)^  {command}\s")

    def test_demo_help_exposes_every_public_operation(self):
        result = run("python3", str(DEMO), "--help")

        self.assertEqual(result.returncode, 0, result.stderr)
        for command in (
            "list",
            "build",
            "qemu-args",
            "fetch",
            "run",
            "verify",
            "verify-repeat",
            "verify-all",
            "debug",
        ):
            self.assertIn(command, result.stdout)

    def test_qemu_board_arguments_are_machine_readable_and_stable(self):
        result = run("python3", str(DEMO), "qemu-args")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip().split(),
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
        self.assertEqual(result.stderr, "")


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
        self.assertTrue(TASK.stat().st_mode & 0o111)
        self.assertTrue(DEMO.stat().st_mode & 0o111)


if __name__ == "__main__":
    unittest.main()
