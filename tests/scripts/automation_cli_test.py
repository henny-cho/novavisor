"""Unit tests for the shared automation layers."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from nova_cli import build, config, process  # noqa: E402


class BuildTests(unittest.TestCase):
    def test_release_and_explicit_presets_have_one_selector(self):
        self.assertEqual(
            build.selected_preset(release=False, preset=None),
            "aarch64-debug",
        )
        self.assertEqual(
            build.selected_preset(release=True, preset=None),
            "aarch64-release",
        )
        self.assertEqual(
            build.selected_preset(release=True, preset="custom"),
            "custom",
        )

    def test_active_input_is_copied_only_when_content_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.yml"
            destination = root / "build" / "active.yml"
            source.write_text("value: one\n")

            build.sync_active(source, destination)
            first_timestamp = destination.stat().st_mtime_ns
            build.sync_active(source, destination)

            self.assertEqual(destination.read_text(), "value: one\n")
            self.assertEqual(destination.stat().st_mtime_ns, first_timestamp)

            source.write_text("value: two\n")
            os.utime(source, ns=(1_000_000_000, 1_000_000_000))
            build.sync_active(source, destination)
            self.assertEqual(destination.read_text(), "value: two\n")
            self.assertGreater(
                destination.stat().st_mtime_ns,
                source.stat().st_mtime_ns,
            )


class ProcessTests(unittest.TestCase):
    @mock.patch("nova_cli.process.subprocess.run")
    def test_commands_share_repository_cwd_and_environment(self, run):
        run.return_value = subprocess.CompletedProcess(["true"], 0)

        process.run(["true"])

        _, kwargs = run.call_args
        self.assertEqual(kwargs["cwd"], config.REPO)
        self.assertEqual(
            kwargs["env"]["CPM_SOURCE_CACHE"],
            str(config.REPO / "external" / "cache" / "cpm"),
        )


class CliTests(unittest.TestCase):
    def test_unknown_build_option_fails_before_build(self):
        result = subprocess.run(
            [str(REPO / "scripts" / "nova"), "build", "--unknown"],
            cwd=REPO,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("unrecognized arguments", result.stderr)


if __name__ == "__main__":
    unittest.main()
