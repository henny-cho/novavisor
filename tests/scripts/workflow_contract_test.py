"""Workflow security and lane ownership contracts."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
GITHUB = REPO / ".github"
WORKFLOWS = GITHUB / "workflows"
PINNED_IMAGE = re.compile(r"image:\s*(\S+@sha256:[0-9a-f]{64})")


def pinned_images() -> set[str]:
    return {
        digest
        for path in WORKFLOWS.glob("*.yml")
        for digest in PINNED_IMAGE.findall(path.read_text())
    }


class WorkflowContractTest(unittest.TestCase):
    def test_external_actions_are_pinned_to_commits(self):
        paths = [*WORKFLOWS.glob("*.yml"), *GITHUB.glob("actions/*/action.yml")]
        for path in paths:
            for action in re.findall(r"uses:\s*([^\s#]+)", path.read_text()):
                if action.startswith("./"):
                    continue
                with self.subTest(path=path, action=action):
                    self.assertRegex(action, r"@[\da-f]{40}$")

    def test_every_container_pins_the_same_immutable_image(self):
        # Pinning the value here would make this a third copy to bump; what
        # matters is that the lanes and the soak never diverge.
        self.assertEqual(len(pinned_images()), 1, pinned_images())
        for name in ("ci.yml", "soak.yml"):
            with self.subTest(workflow=name):
                text = (WORKFLOWS / name).read_text()
                self.assertEqual(len(PINNED_IMAGE.findall(text)), 1)

    def test_the_publisher_reports_the_digest_its_consumers_must_pin(self):
        # dependabot watches the Dockerfile base image, not the digest the
        # consumers pin, so nothing else can notice a stale pin.
        publisher = yaml.safe_load((WORKFLOWS / "toolchain-image.yml").read_text())

        self.assertEqual(
            publisher["permissions"],
            {"contents": "read", "packages": "write"},
        )
        steps = publisher["jobs"]["publish"]["steps"]
        report = next(step for step in steps if step.get("name", "").endswith("digest bump"))
        self.assertEqual(report["env"]["DIGEST"], "${{ steps.build.outputs.digest }}")
        self.assertIn("GITHUB_STEP_SUMMARY", report["run"])

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
        self.assertIn(lanes["container"]["image"], pinned_images())
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
