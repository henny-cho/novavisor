"""Single-source environment and toolchain image contracts."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BOOTSTRAP = REPO / "scripts" / "bootstrap"
PYTHON_ENV = REPO / "scripts" / "python-env"
CLI_REQUIREMENTS = REPO / "scripts" / "requirements-cli.txt"
IMAGE = REPO / "containers" / "toolchain" / "Dockerfile"
DEVCONTAINER = REPO / ".devcontainer" / "devcontainer.json"
sys.path.insert(0, str(REPO / "scripts"))

from novakit.core import config  # noqa: E402


class ToolVersionTests(unittest.TestCase):
    def test_the_image_is_pinned_to_one_base(self):
        # Nothing else fails if this floats: the image would still build,
        # from a different Ubuntu than the one the pins were chosen for.
        self.assertTrue(config.tool_versions())
        self.assertTrue(BOOTSTRAP.stat().st_mode & 0o111)
        self.assertRegex(
            IMAGE.read_text().splitlines()[0],
            r"^FROM ubuntu:26\.04@sha256:[0-9a-f]{64}$",
        )

    def test_python_cli_environment_is_reproducible(self):
        # An unpinned requirement resolves to whatever is newest on the
        # day the image is built, which is how two machines diverge.
        requirements = CLI_REQUIREMENTS.read_text().splitlines()
        self.assertTrue(PYTHON_ENV.stat().st_mode & 0o111)
        self.assertTrue(requirements)
        for requirement in requirements:
            self.assertRegex(requirement, r"^[A-Za-z][A-Za-z0-9-]*==\d+(?:\.\d+)+$")

    def test_devcontainer_builds_the_shared_image(self):
        data = json.loads(
            "\n".join(
                line for line in DEVCONTAINER.read_text().splitlines()
                if not line.lstrip().startswith("//")
            )
        )
        self.assertEqual(
            data["build"]["dockerfile"],
            "../containers/toolchain/Dockerfile",
        )
        self.assertEqual(data["remoteUser"], "nova")


if __name__ == "__main__":
    unittest.main()
