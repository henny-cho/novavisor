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
        soak = (WORKFLOWS / "recovery-soak.yml").read_text()

        self.assertEqual(ci.count(f"image: {IMAGE}"), 1)
        self.assertEqual(soak.count(f"image: {IMAGE}"), 1)

    def test_ci_permissions_and_public_handlers_are_minimal(self):
        ci = yaml.safe_load((WORKFLOWS / "ci.yml").read_text())
        self.assertEqual(
            ci["permissions"],
            {"contents": "read", "packages": "read"},
        )

        text = (WORKFLOWS / "ci.yml").read_text()
        self.assertIn("scripts/nova ci host", text)
        self.assertIn("scripts/nova ci ${{ matrix.lane }}", text)
        for lane in ("static", "runtime"):
            self.assertIn(f"lane: {lane}", text)
        self.assertNotIn(f"scripts/{'task'}.sh", text)

    def test_cache_action_has_no_provisioning(self):
        action = (GITHUB / "actions" / "cache-build" / "action.yml").read_text()

        self.assertNotIn("bootstrap", action)
        self.assertNotIn(".toolchain", action)
        for path in (
            "external/cache/cpm",
            "external/cache/firmware",
            "external/cache/guests",
            "~/.cache/ccache",
        ):
            self.assertIn(path, action)


if __name__ == "__main__":
    unittest.main()
