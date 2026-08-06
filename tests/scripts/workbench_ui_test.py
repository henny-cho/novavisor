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
# A number no observed value legitimately reaches: a sentinel compare.
BIG_LITERAL = re.compile(r"\b(?:\d{16,}|\d(?:\.\d+)?e(?:1[5-9]|[2-9]\d))\b")


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

    def test_no_module_tests_for_a_firmware_sentinel(self):
        # The bridge decodes the firmware's all-bits-set "none" to null.
        # A UI comparing against 9e15 instead is relying on JSON losing
        # precision past 2^53 — right by accident, and only until a
        # sentinel narrower than 53 bits appears.
        for module in sorted((UI / "js").glob("*.mjs")):
            with self.subTest(module=module.name):
                self.assertFalse(BIG_LITERAL.findall(module.read_text()))

    def test_no_module_hardcodes_a_taxonomy_badge(self):
        # The thin-client rule: vocabulary arrives in the topo snapshot.
        for module in sorted((UI / "js").glob("*.mjs")):
            text = module.read_text()
            for badge in Badge:
                with self.subTest(module=module.name, badge=badge.value):
                    self.assertNotIn(f'"{badge.value}"', text)
                    self.assertNotIn(f"'{badge.value}'", text)


VIEW_HEADER = re.compile(r'<div class="view-h">(.*?)</div>\s*<div class="board"', re.S)
# Any literal that looks like a hardware address or an interrupt number.
HARD_ADDRESS = re.compile(r"0x[0-9a-fA-F]{6,}")
# `"topic": ["section", ...]`
PAINTS = re.compile(r'"([\w.]+)":\s*\[([^\]]*)\]')
# A sample rate written into the UI instead of read from the manifest.
RATE_LITERAL = re.compile(r"\d+\s*Hz")


class BoardViewTest(unittest.TestCase):
    """The board draws structure it is given, and stays reachable."""

    def test_the_fold_control_survives_folding(self):
        # Hiding the whole view hides the button that unfolds it, which
        # strands the reader with no way back. The control lives in the
        # header and the rule collapses only the body.
        markup = (UI / "index.html").read_text()
        header = VIEW_HEADER.search(markup)
        self.assertIsNotNone(header, "board view header not found")
        self.assertIn('id="fold"', header.group(1))
        css = (UI / "css" / "workbench.css").read_text()
        self.assertRegex(css, r"\.view\.folded\s*>\s*\.board\s*\{[^}]*display:\s*none")
        self.assertNotRegex(css, r"\.view\[hidden\]")

    def test_the_board_states_no_hardware_value_of_its_own(self):
        # Addresses reach the UI in topo.board, generated from the same
        # headers the linker script reads. One typed into the module
        # would drift with no way for the browser to notice.
        source = (UI / "js" / "board.mjs").read_text()
        self.assertFalse(HARD_ADDRESS.findall(source), "board.mjs hardcodes an address")

    def test_the_board_reads_only_published_topics(self):
        # Its topic table is the contract with the observation manifest;
        # a topic the bridge never publishes would silently draw nothing.
        from novakit.services.workbench.observations import OBSERVATIONS

        source = (UI / "js" / "board.mjs").read_text()
        table = re.search(r"const TOPICS = \{(.*?)\n\};", source, re.S)
        self.assertIsNotNone(table, "board topic table not found")
        wanted = set(re.findall(r'"([\w.]+)":', table.group(1)))
        self.assertTrue(wanted)
        published = {obs.topic for obs in OBSERVATIONS}
        self.assertLessEqual(wanted, published, f"unpublished: {wanted - published}")

    def test_the_board_states_no_sample_rate_of_its_own(self):
        # A badge reading "S 20Hz" is a claim about the manifest. Written
        # here it becomes a lie the moment a rate is tuned, and the
        # screen goes on asserting it. The rate rides in topo.
        source = (UI / "js" / "board.mjs").read_text()
        stated = [hit for hit in RATE_LITERAL.findall(source) if not hit.startswith("$")]
        self.assertFalse(stated, f"board.mjs states a rate: {stated}")

    def test_a_topic_repaints_named_sections_and_nothing_more(self):
        # Twenty scheduler samples a second must not redraw the address
        # strip, so each topic declares the sections it can change. A
        # section with no painter behind it is a silent no-op, and a
        # painter no topic names is a value that stopped arriving.
        source = (UI / "js" / "board.mjs").read_text()
        table = re.search(r"const TOPICS = \{(.*?)\n\};", source, re.S)
        self.assertIsNotNone(table, "board topic table not found")
        painters = set(re.findall(r"^ {4}(\w+): render\w+,$", source, re.M))
        self.assertTrue(painters, "board painter table not found")
        entries = PAINTS.findall(table.group(1))
        self.assertEqual(
            len(entries), len(re.findall(r'"[\w.]+":', table.group(1))), "a topic paints nothing"
        )
        claimed = set()
        for topic, listed in entries:
            sections = set(re.findall(r'"(\w+)"', listed))
            with self.subTest(topic=topic):
                self.assertTrue(sections, "subscribed but paints nothing")
                self.assertLessEqual(sections, painters, f"no painter: {sections - painters}")
            claimed |= sections
        self.assertEqual(painters, claimed, f"painters no topic reaches: {painters - claimed}")


TYPE_SCALE = ("--fs-title", "--fs-body", "--fs-meta", "--fs-label")
# `font-size: X` and the size slot of the `font:` shorthand.
FONT_SIZE = re.compile(r"font-size:\s*([^;}]+)|font:\s*(?:[\w\s]*?\s)?((?:var\(--fs-[\w-]+\)|[\d.]+px))")
# A wordmark is not text on the page; it is allowed its own size.
SCALE_EXEMPT = (".brand .nv",)


class TypeScaleTest(unittest.TestCase):
    """Four sizes, no fifth.

    A dense screen reads as noise long before any single value is too
    small, and the drift is invisible in review — the numbers differ by
    half a pixel. Naming the four roles makes a fifth size fail here.
    """

    def test_the_scale_is_declared_once(self):
        css = (UI / "css" / "workbench.css").read_text()
        declared = dict(CUSTOM_PROPERTY.findall(css))
        for name in TYPE_SCALE:
            with self.subTest(token=name):
                self.assertIn(name, declared)
        values = [declared[name].strip() for name in TYPE_SCALE]
        self.assertEqual(len(set(values)), len(values), f"duplicate steps: {values}")

    def test_every_rule_picks_a_step(self):
        css = (UI / "css" / "workbench.css").read_text()
        for rule in css.split("}"):
            selector = rule.rsplit("{", 1)[0].strip().splitlines()[-1:] or [""]
            if selector[0].strip() in SCALE_EXEMPT:
                continue
            for explicit, shorthand in FONT_SIZE.findall(rule):
                size = (explicit or shorthand).strip()
                if not size or size == "inherit":
                    continue
                with self.subTest(selector=selector[0].strip(), size=size):
                    self.assertTrue(
                        any(f"var({name})" in size for name in TYPE_SCALE),
                        f"{size} is not a step of the scale",
                    )


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


class PanelStripTest(unittest.TestCase):
    """The control strip over the measurement drawer."""

    def strip(self) -> str:
        markup = (UI / "index.html").read_text()
        found = re.search(r'<div class="([^"]*)" id="panel-tabs"[^>]*>', markup)
        self.assertIsNotNone(found, "panel strip not found")
        return found.group(0)

    def test_the_strip_wraps(self):
        # Unlike the console tabs (data-driven, at most a few), the panel
        # controls are a fixed set wider than the column. Scrolling them is
        # invisible — `.tabs` hides its scrollbar — so they must wrap.
        self.assertIn("wrap", re.search(r'class="([^"]*)"', self.strip()).group(1).split())
        css = (UI / "css" / "workbench.css").read_text()
        self.assertRegex(css, r"\.tabs\.wrap\s*\{[^}]*flex-wrap:\s*wrap")

    def test_the_strip_is_a_toggle_group(self):
        # Several panels may be open at once. role="tablist" promises
        # single selection to assistive tech and would be a lie the moment
        # a second panel is switched on.
        self.assertIn('role="group"', self.strip())
        panels = (UI / "js" / "panels.mjs").read_text()
        self.assertIn("aria-pressed", panels)
        self.assertNotIn("aria-selected", panels)


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
