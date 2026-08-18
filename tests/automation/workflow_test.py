"""Workflow security and lane ownership contracts."""

from __future__ import annotations

import re
import unittest

import yaml

from novakit.services import ci as ci_service
from tests import REPO

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

    def test_no_checkout_persists_credentials(self):
        # No workflow pushes, and the guest fetch scripts clone anonymously,
        # so a token left in .git/config only widens what a later step reaches.
        for path in WORKFLOWS.glob("*.yml"):
            checkouts = [
                step
                for job in yaml.safe_load(path.read_text())["jobs"].values()
                for step in job.get("steps", [])
                if str(step.get("uses", "")).startswith("actions/checkout@")
            ]
            with self.subTest(workflow=path.name):
                self.assertTrue(checkouts)
                for step in checkouts:
                    self.assertIs(step["with"]["persist-credentials"], False)

    def test_ci_permissions_and_public_handlers_are_minimal(self):
        ci = yaml.safe_load((WORKFLOWS / "ci.yml").read_text())
        self.assertEqual(
            ci["permissions"],
            {"contents": "read", "packages": "read"},
        )

        text = (WORKFLOWS / "ci.yml").read_text()
        self.assertIn("./nova ci ${{ matrix.lane }}", text)
        for lane in ci_service.BY_NAME:
            self.assertIn(f"lane: {lane}", text)

    def test_every_lane_runs_the_same_way(self):
        # Two job definitions for the same kind of work is how the pinned
        # toolchain grew a second provisioning path once before.
        ci = yaml.safe_load((WORKFLOWS / "ci.yml").read_text())
        lanes = ci["jobs"]["lane"]

        self.assertEqual(sorted(ci["jobs"]), ["ci", "lane"])
        self.assertIn(lanes["container"]["image"], pinned_images())
        self.assertEqual(
            [entry["lane"] for entry in lanes["strategy"]["matrix"]["include"]],
            [lane.name for lane in ci_service.LANES],
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

    def test_lane_action_derives_its_caches_and_reuses_python_setup(self):
        action = (GITHUB / "actions" / "lane" / "action.yml").read_text()

        self.assertNotIn("bootstrap", action)
        self.assertNotIn(".toolchain", action)
        self.assertIn("novakit/python-env", action)
        self.assertIn("NOVA_PYTHON=", action)
        self.assertIn("./nova ci --metadata", action)
        data = yaml.safe_load(action)
        self.assertEqual(list(data["inputs"]), ["name"])
        for path in (
            "external/cache/cpm",
            "external/cache/firmware",
            "external/cache/guests",
            "~/.cache/ccache",
        ):
            self.assertIn(path, action)
        cache = next(
            step for step in data["runs"]["steps"] if step.get("name") == "Cache ccache"
        )
        for field in ("key", "restore-keys"):
            self.assertIn(
                "${{ steps.lane.outputs.cache_scope }}",
                cache["with"][field],
            )

    def test_the_lane_action_knows_every_lane_and_soak_target(self):
        # All lanes and soak targets must be registered in the CI metadata table,
        # which provides the single source of truth for workflow environments.
        soak = yaml.safe_load((WORKFLOWS / "soak.yml").read_text())
        targets = [
            f"soak-{entry['target']}"
            for entry in soak["jobs"]["soak"]["strategy"]["matrix"]["include"]
        ]

        self.assertTrue(targets)
        for name in (*ci_service.BY_NAME, *targets):
            with self.subTest(lane=name):
                meta = ci_service.lane_metadata(name)
                self.assertIn("cache_scope", meta)
                self.assertIn("compiler", meta)
                self.assertIn("timeout_minutes", meta)

    def test_ci_workflow_matrix_matches_lane_timeout_ssot(self):
        ci_doc = yaml.safe_load((WORKFLOWS / "ci.yml").read_text())
        matrix_includes = ci_doc["jobs"]["lane"]["strategy"]["matrix"]["include"]
        for entry in matrix_includes:
            lane_name = entry["lane"]
            yaml_timeout = entry["timeout"]
            ssot_lane = ci_service.BY_NAME[lane_name]
            with self.subTest(lane=lane_name):
                self.assertEqual(yaml_timeout, ssot_lane.timeout_minutes)
