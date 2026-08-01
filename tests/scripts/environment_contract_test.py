"""Single-source environment and toolchain image contracts."""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
VERSIONS = REPO / "scripts" / "tool-versions.env"
BOOTSTRAP = REPO / "scripts" / "bootstrap"
PYTHON_ENV = REPO / "scripts" / "python-env"
CLI_REQUIREMENTS = REPO / "scripts" / "requirements-cli.txt"
IMAGE = REPO / "containers" / "toolchain" / "Dockerfile"
DEVCONTAINER = REPO / ".devcontainer" / "devcontainer.json"
sys.path.insert(0, str(REPO / "scripts"))

from novakit.core import config  # noqa: E402


class ToolVersionTests(unittest.TestCase):
    def test_version_source_contains_data_only(self):
        entries = {}
        for line in VERSIONS.read_text().splitlines():
            match = re.fullmatch(r"([A-Z][A-Z0-9_]*)=([^\s#]+)", line)
            self.assertIsNotNone(match, line)
            entries[match.group(1)] = match.group(2)

        required = {
            "ARM_GNU_VERSION",
            "ARM_GNU_SHA256_LINUX_X86_64",
            "ARM_GNU_SHA256_LINUX_AARCH64",
            "ARM_GNU_SHA256_DARWIN_ARM64",
            "CLANG_FORMAT_VERSION",
            "CLANG_TIDY_VERSION",
            "QEMU_MIN_VERSION",
            "TFA_VERSION",
            "TFA_COMMIT",
            "RUFF_VERSION",
            "ACTIONLINT_VERSION",
        }
        self.assertEqual(set(entries), required)
        for name in required:
            self.assertEqual(config.tool_version(name), entries[name])
            if "_SHA256_" in name:
                self.assertRegex(entries[name], r"^[0-9a-f]{64}$")

    def test_public_bootstrap_and_image_share_the_version_source(self):
        self.assertTrue(BOOTSTRAP.stat().st_mode & 0o111)
        self.assertIn("source \"${REPO}/scripts/tool-versions.env\"", BOOTSTRAP.read_text())
        self.assertIn("scripts/tool-versions.env", IMAGE.read_text())
        self.assertIn("scripts/bootstrap --image", IMAGE.read_text())
        self.assertRegex(
            IMAGE.read_text().splitlines()[0],
            r"^FROM ubuntu:26\.04@sha256:[0-9a-f]{64}$",
        )
        self.assertIn(
            'verify_sha256 "${TOOLCHAIN_SHA256}" "${archive}"',
            BOOTSTRAP.read_text(),
        )

    def test_python_cli_environment_is_reproducible(self):
        requirements = CLI_REQUIREMENTS.read_text().splitlines()

        self.assertTrue(PYTHON_ENV.stat().st_mode & 0o111)
        self.assertTrue(requirements)
        for requirement in requirements:
            self.assertRegex(requirement, r"^[A-Za-z][A-Za-z0-9-]*==\d+(?:\.\d+)+$")
        self.assertIn("typer==0.27.0", requirements)
        self.assertIn(
            'scripts/python-env" "${TOOLCHAIN_ROOT}/python"', BOOTSTRAP.read_text()
        )
        self.assertIn("scripts/requirements-cli.txt", IMAGE.read_text())

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
