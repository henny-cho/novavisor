"""Structural contracts of the build-step-free workbench UI.

Without a bundler, a broken import path or a token drift is invisible
until a browser opens the page; these checks make both fail in CI.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from novakit.services.workbench.taxonomy import Badge  # noqa: E402

UI = REPO / "web_sim" / "workbench"
SIM = REPO / "web_sim" / "novavisor-sim.html"

HTML_REFERENCE = re.compile(r'(?:src|href)="([^"]+)"')
MODULE_IMPORT = re.compile(r'(?:import|from)\s+"(\./[^"]+)"')
CUSTOM_PROPERTY = re.compile(r"(--[\w-]+)\s*:\s*([^;]+);")


class UiStructureTest(unittest.TestCase):
    def test_every_referenced_asset_exists(self):
        index = UI / "index.html"
        self.assertTrue(index.is_file(), "workbench UI entry point is missing")
        for reference in HTML_REFERENCE.findall(index.read_text()):
            if reference.startswith(("http", "//", "#", "data:")):
                self.fail(f"external reference in a self-contained UI: {reference}")
            with self.subTest(reference=reference):
                self.assertTrue((UI / reference).is_file(), reference)

    def test_every_module_import_resolves(self):
        modules = sorted((UI / "js").glob("*.mjs"))
        self.assertTrue(modules, "no UI modules found")
        for module in modules:
            for target in MODULE_IMPORT.findall(module.read_text()):
                with self.subTest(module=module.name, target=target):
                    self.assertTrue((module.parent / target).is_file(), target)

    def test_no_module_hardcodes_a_taxonomy_badge(self):
        # The thin-client rule: vocabulary arrives in the topo snapshot.
        for module in sorted((UI / "js").glob("*.mjs")):
            text = module.read_text()
            for badge in Badge:
                with self.subTest(module=module.name, badge=badge.value):
                    self.assertNotIn(f'"{badge.value}"', text)
                    self.assertNotIn(f"'{badge.value}'", text)


class TokenParityTest(unittest.TestCase):
    """tokens.css is authoritative; the frozen sim must agree with it."""

    @unittest.skipUnless(SIM.is_file(), "frozen simulator is local-only")
    def test_sim_palette_matches_the_shared_tokens(self):
        # A themed token is declared once per theme on both sides, so parity
        # is a question about (name, value) pairs: every declaration the sim
        # makes under a shared name must exist verbatim in the tokens file.
        declarations = CUSTOM_PROPERTY.findall((UI / "css" / "tokens.css").read_text())
        pairs = {(name, value.strip()) for name, value in declarations}
        names = {name for name, _ in declarations}
        sim_properties = CUSTOM_PROPERTY.findall(SIM.read_text())
        self.assertTrue(pairs and sim_properties)
        shared = [(name, value.strip()) for name, value in sim_properties if name in names]
        self.assertTrue(shared, "no shared custom properties between sim and tokens")
        for declaration in shared:
            with self.subTest(token=declaration[0]):
                self.assertIn(declaration, pairs)


if __name__ == "__main__":
    unittest.main()
