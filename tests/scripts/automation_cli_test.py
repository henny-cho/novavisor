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

from nova_cli import build, checks, config, process  # noqa: E402


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

    def test_ci_help_exposes_explicit_lanes(self):
        result = subprocess.run(
            [str(REPO / "scripts" / "nova"), "ci", "--help"],
            cwd=REPO,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("{host,static,runtime,all}", result.stdout)

    def test_ci_all_dispatches_each_lane_once(self):
        calls = []
        with (
            mock.patch.object(
                checks,
                "format_sources",
                side_effect=lambda **_kwargs: calls.append("format") or 0,
            ),
            mock.patch.object(
                checks,
                "test",
                side_effect=lambda: calls.append("tests") or 0,
            ),
            mock.patch.object(
                checks,
                "static_checks",
                side_effect=lambda: calls.append("static") or 0,
            ),
            mock.patch.object(
                checks,
                "runtime_checks",
                side_effect=lambda: calls.append("runtime") or 0,
            ),
        ):
            self.assertEqual(checks.ci("all"), 0)

        self.assertEqual(calls, ["format", "tests", "static", "runtime"])

    @mock.patch("nova_cli.checks.shutil.which", return_value="/usr/bin/ccache")
    @mock.patch("nova_cli.checks.process.run")
    def test_ci_summary_includes_timing_and_ccache_stats(self, run, _which):
        run.return_value = subprocess.CompletedProcess(
            ["ccache", "--show-stats"],
            0,
            stdout="Cacheable calls: 10\nHits: 8\n",
        )
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / "summary.md"
            with mock.patch.dict(
                os.environ,
                {"GITHUB_STEP_SUMMARY": str(summary)},
            ):
                checks._append_ci_summary(
                    "static",
                    [("static/static-analysis", "pass", 1.25)],
                )

            content = summary.read_text()

        self.assertIn("| static/static-analysis | pass | 1.2 |", content)
        self.assertIn("Cacheable calls: 10", content)

    @mock.patch("nova_cli.checks.shutil.which", return_value=None)
    def test_ci_summary_allows_host_without_ccache(self, _which):
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / "summary.md"
            with mock.patch.dict(
                os.environ,
                {"GITHUB_STEP_SUMMARY": str(summary)},
            ):
                checks._append_ci_summary("host", [("host/tests", "pass", 1.0)])

            content = summary.read_text()

        self.assertIn("| host/tests | pass | 1.0 |", content)


if __name__ == "__main__":
    unittest.main()
