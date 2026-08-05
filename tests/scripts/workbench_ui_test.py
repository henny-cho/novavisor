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

UI = REPO / "web" / "workbench"
# The design mock the palette came from: local-only, never tracked.
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


SIDE_COLUMN = re.compile(r'<aside class="side">(.*?)</aside>', re.S)
SIDE_RULE = re.compile(r"\.side\s*\{([^}]*)\}")
GRID_ROWS = re.compile(r"grid-template-rows:([^;}]*)")
TRACK = re.compile(r"minmax\([^)]*\)|\S+")


class SideColumnLayoutTest(unittest.TestCase):
    """The right column stacks independent panes; each needs a size."""

    def test_every_pane_is_sized_explicitly(self):
        # A pane that falls into an implicit grid row is sized by its own
        # content, and the event log grows without bound — it squeezed the
        # flexible track (the panel drawer) to nothing, so the panels
        # blinked in and out and their tab strip lost its scroll offset.
        side = SIDE_COLUMN.search((UI / "index.html").read_text())
        self.assertIsNotNone(side, "side column markup not found")
        panes = len(re.findall(r'<section class="pane"', side.group(1)))
        self.assertGreaterEqual(panes, 3)

        css = (UI / "css" / "workbench.css").read_text()
        rule = SIDE_RULE.search(css)
        self.assertIsNotNone(rule, ".side rule not found")
        declarations = rule.group(1).replace(" ", "")
        rows = GRID_ROWS.search(declarations)
        if rows:
            tracks = len(TRACK.findall(rows.group(1)))
            self.assertGreaterEqual(
                tracks, panes, f"{panes} panes but {tracks} row tracks"
            )
        else:
            self.assertIn("display:flex", declarations)
            self.assertRegex(css, r"\.side\s*>\s*\.pane:last-child\s*\{[^}]*flex:\s*1")

    def test_the_panel_tab_strip_wraps(self):
        # Unlike the console tabs (data-driven, at most a few), the panel
        # tabs are a fixed set wider than the column. Scrolling them is
        # invisible — `.tabs` hides its scrollbar — so they must wrap.
        markup = (UI / "index.html").read_text()
        strip = re.search(r'<div class="([^"]*)" id="panel-tabs"', markup)
        self.assertIsNotNone(strip, "panel tab strip not found")
        self.assertIn("wrap", strip.group(1).split())
        css = (UI / "css" / "workbench.css").read_text()
        self.assertRegex(css, r"\.tabs\.wrap\s*\{[^}]*flex-wrap:\s*wrap")


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
