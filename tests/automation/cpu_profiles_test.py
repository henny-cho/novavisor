"""Which builds may run where, and why a name comparison cannot say.

A build is runnable on a runtime when the runtime provides everything
the build requires. The interesting case is the one an equality test
gets wrong: a baseline binary on a superset core, which must be allowed.

Two readers share `cpu_profiles.json`, so the same pairing is decided
the same way whether CMake or Python asks. Both are exercised here — the
Python model directly, and the CMake side through a real configure,
because a second implementation that agreed only in review is the thing
a single source is meant to prevent.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from novakit.core import config, cpu_profiles
from tests import REPO


class ProfileDataTest(unittest.TestCase):
    """What the file has to say for the readers to mean anything."""

    def setUp(self):
        self.document = json.loads(cpu_profiles.PROFILES.read_text())

    def test_the_file_sits_with_the_architecture_it_describes(self):
        self.assertTrue(cpu_profiles.PROFILES.is_file())
        self.assertEqual(
            cpu_profiles.PROFILES.relative_to(config.REPO).as_posix(),
            "src/hal/arch/aarch64/cpu_profiles.json",
        )

    def test_every_declared_profile_loads(self):
        for name in cpu_profiles.names("build"):
            self.assertTrue(cpu_profiles.build_profile(name).mcpu)
        for name in cpu_profiles.names("runtime"):
            self.assertTrue(cpu_profiles.runtime_profile(name).qemu)

    def test_each_board_names_profiles_the_file_declares(self):
        """A manifest pointing at a profile that is gone breaks configure."""
        for manifest in sorted(REPO.glob("src/hal/board/*/board.cmake")):
            text = manifest.read_text()
            for kind, key in (("build", "BUILD"), ("runtime", "RUNTIME")):
                declared = [
                    line.split('"')[1]
                    for line in text.splitlines()
                    if f"NOVA_BOARD_{key}_CPU_PROFILE" in line
                ]
                with self.subTest(board=manifest.parent.name, kind=kind):
                    self.assertEqual(len(declared), 1, "each board picks exactly one")
                    self.assertIn(declared[0], cpu_profiles.names(kind))

    def test_the_old_free_form_cpu_strings_are_gone(self):
        """Three declarations were the defect; leaving one keeps it."""
        for pattern in ("NOVA_BOARD_REQUIRED_CPU", "NOVA_BOARD_CPU"):
            found = [
                path.relative_to(REPO).as_posix()
                for path in (*REPO.glob("src/hal/board/*/board.cmake"),
                             *REPO.glob("cmake/*.cmake"))
                if pattern in path.read_text()
            ]
            self.assertEqual(found, [], f"{pattern} still declared")


class InclusionTest(unittest.TestCase):
    """The rule itself, on the pairings that distinguish it."""

    def pair(self, build: str, runtime: str):
        return cpu_profiles.build_profile(build), cpu_profiles.runtime_profile(runtime)

    def test_a_matching_pair_runs(self):
        cpu_profiles.require_compatible(*self.pair("n1-tuned", "n1"))

    def test_a_baseline_build_runs_on_a_superset_core(self):
        """The case an equality comparison would wrongly reject."""
        build, runtime = self.pair("a57-baseline", "n1")
        self.assertNotEqual(build.name, runtime.name)
        self.assertEqual(cpu_profiles.missing(build, runtime), frozenset())
        cpu_profiles.require_compatible(build, runtime)

    def test_a_tuned_build_is_refused_on_a_baseline_core(self):
        build, runtime = self.pair("n1-tuned", "a57")
        self.assertEqual(
            cpu_profiles.missing(build, runtime), frozenset({"neoverse-n1-codegen"}))
        with self.assertRaises(cpu_profiles.Incompatible) as refusal:
            cpu_profiles.require_compatible(build, runtime)
        self.assertIn("neoverse-n1-codegen", str(refusal.exception))

    def test_the_refusal_names_the_missing_token_not_the_cpu(self):
        """The reader has to know what to change, not merely that it failed."""
        with self.assertRaises(cpu_profiles.Incompatible) as refusal:
            cpu_profiles.require_compatible(*self.pair("n1-tuned", "a57"))
        self.assertNotIn("neoverse-n1 ", str(refusal.exception))

    def test_an_unknown_profile_says_which_ones_exist(self):
        with self.assertRaises(cpu_profiles.UnknownProfile) as absent:
            cpu_profiles.build_profile("nonesuch")
        self.assertIn("a57-baseline", str(absent.exception))


class CMakeReaderTest(unittest.TestCase):
    """The other reader, run for real against the same file."""

    def configure(self, script: str) -> subprocess.CompletedProcess:
        directory = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="nova-cpu-")))
        (directory / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.25)\n"
            "project(cpu_profiles_probe NONE)\n"
            f'include("{REPO / "cmake" / "nova_cpu_profiles.cmake"}")\n'
            f"{script}\n"
        )
        return subprocess.run(
            ["cmake", "-S", str(directory), "-B", str(directory / "b")],
            capture_output=True, text=True,
        )

    def test_it_reads_the_same_flag_python_reads(self):
        answer = self.configure(
            'nova_cpu_profile_field(build n1-tuned mcpu M)\nmessage(STATUS "M=${M}")')
        self.assertEqual(answer.returncode, 0, answer.stdout + answer.stderr)
        self.assertIn(f"M={cpu_profiles.build_profile('n1-tuned').mcpu}", answer.stdout)

    def test_it_allows_the_same_subset_pairing_python_allows(self):
        answer = self.configure("nova_validate_cpu_profiles(a57-baseline n1)")
        self.assertEqual(answer.returncode, 0, answer.stdout + answer.stderr)

    def test_it_refuses_the_same_pairing_python_refuses_and_names_the_token(self):
        answer = self.configure("nova_validate_cpu_profiles(n1-tuned a57)")
        self.assertNotEqual(answer.returncode, 0)
        self.assertIn("neoverse-n1-codegen", answer.stdout + answer.stderr)

    def test_it_refuses_a_profile_the_file_does_not_declare(self):
        answer = self.configure("nova_cpu_profile_field(build nonesuch mcpu M)")
        self.assertNotEqual(answer.returncode, 0)
        self.assertIn("nonesuch", answer.stdout + answer.stderr)


if __name__ == "__main__":
    unittest.main()
