"""Workflow security and lane ownership contracts."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
GITHUB = REPO / ".github"
WORKFLOWS = GITHUB / "workflows"
IMAGE = (
    "ghcr.io/henny-cho/novavisor-toolchain"
    "@sha256:3f3b10be1424abf99b04d6d3a9fc0c8253252f750b3d8e1db4cadd9ac3609078"
)


class WorkflowContractTest(unittest.TestCase):
    def test_external_actions_are_pinned_to_commits(self):
        paths = [*WORKFLOWS.glob("*.yml"), *GITHUB.glob("actions/*/action.yml")]
        for path in paths:
            for action in re.findall(r"uses:\s*([^\s#]+)", path.read_text()):
                if action.startswith("./"):
                    continue
                with self.subTest(path=path, action=action):
                    self.assertRegex(action, r"@[\da-f]{40}$")

    def test_ci_and_soak_use_the_same_immutable_image(self):
        ci = (WORKFLOWS / "ci.yml").read_text()
        soak = (WORKFLOWS / "soak.yml").read_text()

        self.assertEqual(ci.count(f"image: {IMAGE}"), 1)
        self.assertEqual(soak.count(f"image: {IMAGE}"), 1)

    def test_ci_permissions_and_public_handlers_are_minimal(self):
        ci = yaml.safe_load((WORKFLOWS / "ci.yml").read_text())
        self.assertEqual(
            ci["permissions"],
            {"contents": "read", "packages": "read"},
        )

        text = (WORKFLOWS / "ci.yml").read_text()
        self.assertIn("scripts/nova ci ${{ matrix.lane }}", text)
        for lane in ("host", "static", "runtime"):
            self.assertIn(f"lane: {lane}", text)
        self.assertNotIn(f"scripts/{'task'}.sh", text)

    def test_every_lane_runs_the_same_way(self):
        # Two job definitions for the same kind of work is how the pinned
        # toolchain grew a second provisioning path once before.
        ci = yaml.safe_load((WORKFLOWS / "ci.yml").read_text())
        lanes = ci["jobs"]["lane"]

        self.assertEqual(sorted(ci["jobs"]), ["ci", "lane"])
        self.assertIn(IMAGE, lanes["container"]["image"])
        self.assertEqual(
            [entry["lane"] for entry in lanes["strategy"]["matrix"]["include"]],
            ["host", "static", "runtime"],
        )

    def test_the_gate_compares_one_matrix_result_without_interpolation(self):
        # A shell line built from ${{ }} is a shell line an expression can
        # rewrite; the comparison reads an env var instead.
        gate = yaml.safe_load((WORKFLOWS / "ci.yml").read_text())["jobs"]["ci"]

        self.assertEqual(gate["env"], {"LANES": "${{ needs.lane.result }}"})
        self.assertEqual(
            [step["run"] for step in gate["steps"]],
            ['test "${LANES}" = "success"'],
        )

    def test_lane_action_derives_its_caches_and_provisions_nothing(self):
        action = (GITHUB / "actions" / "lane" / "action.yml").read_text()

        self.assertNotIn("bootstrap", action)
        self.assertNotIn(".toolchain", action)
        self.assertEqual(list(yaml.safe_load(action)["inputs"]), ["name"])
        for path in (
            "external/cache/cpm",
            "external/cache/firmware",
            "external/cache/guests",
            "~/.cache/ccache",
        ):
            self.assertIn(path, action)

    def test_no_workflow_repeats_what_a_lane_needs(self):
        # guests/firmware follow from the lane name, so a workflow that spells
        # them out has a second copy of that mapping.
        for path in WORKFLOWS.glob("*.yml"):
            with self.subTest(workflow=path.name):
                text = path.read_text()
                self.assertNotIn("guests:", text)
                self.assertNotIn("firmware:", text)


if __name__ == "__main__":
    unittest.main()
