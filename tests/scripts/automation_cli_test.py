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

from novakit.core import config, proc  # noqa: E402
from novakit.services import ci, cmake, gates, report  # noqa: E402


class BuildTests(unittest.TestCase):
    def test_release_and_explicit_presets_have_one_selector(self):
        self.assertEqual(
            cmake.selected_preset(release=False, preset=None),
            "aarch64-debug",
        )
        self.assertEqual(
            cmake.selected_preset(release=True, preset=None),
            "aarch64-release",
        )
        self.assertEqual(
            cmake.selected_preset(release=True, preset="custom"),
            "custom",
        )

    def test_active_input_is_copied_only_when_content_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.yml"
            destination = root / "build" / "active.yml"
            source.write_text("value: one\n")

            cmake.sync_active(source, destination)
            first_timestamp = destination.stat().st_mtime_ns
            cmake.sync_active(source, destination)

            self.assertEqual(destination.read_text(), "value: one\n")
            self.assertEqual(destination.stat().st_mtime_ns, first_timestamp)

            source.write_text("value: two\n")
            os.utime(source, ns=(1_000_000_000, 1_000_000_000))
            cmake.sync_active(source, destination)
            self.assertEqual(destination.read_text(), "value: two\n")
            self.assertGreater(
                destination.stat().st_mtime_ns,
                source.stat().st_mtime_ns,
            )


class ProcessTests(unittest.TestCase):
    @mock.patch("novakit.core.proc.subprocess.run")
    def test_commands_share_repository_cwd_and_environment(self, run):
        run.return_value = subprocess.CompletedProcess(["true"], 0)

        proc.run(["true"])

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
        self.assertIn("No such option", result.stderr)

    def test_build_selectors_are_mutually_exclusive(self):
        result = subprocess.run(
            [
                str(REPO / "scripts" / "nova"),
                "build",
                "--release",
                "--preset",
                "custom",
            ],
            cwd=REPO,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot be used together", result.stderr)

    def test_ci_help_exposes_explicit_lanes(self):
        result = subprocess.run(
            [str(REPO / "scripts" / "nova"), "ci", "--help"],
            cwd=REPO,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("host|static|runtime|all", result.stdout)

    def test_lane_steps_are_uniquely_named(self):
        # A step name is what the CI summary reports and what a failure is
        # attributed to, so two steps may never share one.
        names = [f"{lane.name}/{step}" for lane in ci.LANES for step, _ in lane.steps]
        self.assertEqual(sorted(names), sorted(set(names)))
        self.assertEqual([lane.name for lane in ci.LANES], ["host", "static", "runtime"])

    def test_ci_all_runs_every_lane_step_in_order(self):
        calls = []

        def record(name, result=0):
            return lambda *_args, **_kwargs: (calls.append(name), result)[1]

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        with (
            # run_lane publishes a step summary. Under Actions this test runs
            # inside a real lane, so leaving the variable set appends a table
            # of mocked steps to the job summary of the lane that ran it.
            mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": ""}),
            mock.patch.object(gates, "format_sources", side_effect=record("format")),
            mock.patch.object(gates, "test", side_effect=record("tests")),
            mock.patch.object(gates, "static_analysis", side_effect=record("static")),
            mock.patch.object(cmake, "build", side_effect=record("preset")),
            mock.patch.object(cmake, "BuildSpec"),
            mock.patch.object(ci.tfa, "build_profile", side_effect=record("bl33")),
            mock.patch.object(
                ci.tfa, "verify_chain", side_effect=record("firmware")
            ),
            mock.patch.object(ci.suite, "fetch_all", side_effect=record("fetch")),
            mock.patch.object(ci.suite, "verify_all", side_effect=record("demos")),
            mock.patch.object(ci.suite, "verify_one", side_effect=record("recheck")),
            mock.patch.object(ci, "EVIDENCE", report.ArtifactPaths(Path(directory.name))),
        ):
            self.assertEqual(ci.run_lane("all"), 0)

        self.assertEqual(
            calls,
            [
                "format",
                "tests",
                "static",
                *["preset"] * len(ci.RUNTIME_PRESETS),
                "bl33",
                "firmware",
                "fetch",
                "demos",
                *["recheck"] * len(ci.RUNTIME_RECHECK),
            ],
        )

    @mock.patch("novakit.services.ci.shutil.which", return_value="/usr/bin/ccache")
    @mock.patch("novakit.core.proc.run")
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
                ci._append_summary(
                    "static",
                    [("static/static-analysis", "pass", 1.25)],
                )

            content = summary.read_text()

        self.assertIn("| static/static-analysis | pass | 1.2 |", content)
        self.assertIn("Cacheable calls: 10", content)

    @mock.patch("novakit.services.ci.shutil.which", return_value=None)
    def test_ci_summary_allows_host_without_ccache(self, _which):
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / "summary.md"
            with mock.patch.dict(
                os.environ,
                {"GITHUB_STEP_SUMMARY": str(summary)},
            ):
                ci._append_summary("host", [("host/tests", "pass", 1.0)])

            content = summary.read_text()

        self.assertIn("| host/tests | pass | 1.0 |", content)


if __name__ == "__main__":
    unittest.main()
