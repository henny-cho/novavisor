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


class PanelReachTest(unittest.TestCase):
    """Every published observation is on screen without being drawn."""

    def test_unclaimed_topics_fall_to_a_panel_fed_by_the_manifest(self):
        # Otherwise the default is that an observation is invisible until
        # somebody writes a table for it, and a value can be polled for
        # months with nobody able to see it. The fallback's topic list
        # has to come from the manifest, not from a second list here.
        source = (UI / "js" / "panels.mjs").read_text()
        self.assertRegex(
            source,
            r"FALLBACK\.topics\s*=\s*Object\.keys\(topo\.observations",
            "the fallback panel does not follow the observation manifest",
        )
        self.assertRegex(source, r"filter\(\(topic\)\s*=>\s*!claimed\.has\(topic\)\)")

    def test_no_panel_reads_a_value_without_its_provenance(self):
        """A stop's whole product is what moved, and a renderer handed a
        bare number has already lost it.

        Enforced by removal rather than by review: the accessor that
        returned a value alone is gone, so the only ways into a reading
        are `at()`, which carries the mask, and `plain()`, which states
        out loud that a cell was computed here. Nine renderers were nine
        chances to forget a highlight, and every new panel was another.
        """
        source = (UI / "js" / "panels.mjs").read_text()
        self.assertNotIn("value(", source)
        self.assertRegex(source, r"const at = \(topic\) =>")
        self.assertRegex(source, r"const plain = \(shown\) =>")

    def test_a_table_cell_that_lost_its_provenance_is_refused(self):
        """Not merely unhighlighted — a bare value in a cell would draw
        perfectly and silently never light up, which is the exact
        failure this arrangement exists to make impossible."""
        source = (UI / "js" / "panels.mjs").read_text()
        self.assertRegex(source, r"if \(!\(cell instanceof Cell\)\)[\s\S]{0,200}throw new TypeError")

    def test_the_moved_cell_has_a_style_to_be_seen_by(self):
        css = (UI / "css" / "workbench.css").read_text()
        self.assertRegex(css, r"\.ptable td\.moved")


VIEW_HEADER = re.compile(r'<div class="view-h">(.*?)</div>\s*<div class="board"', re.S)
# Any literal that looks like a hardware address or an interrupt number.
HARD_ADDRESS = re.compile(r"0x[0-9a-fA-F]{6,}")
# `"topic": ["section", ...]`
PAINTS = re.compile(r'"([\w.]+)":\s*\[([^\]]*)\]')
# A sample rate written into the UI instead of read from the manifest.
RATE_LITERAL = re.compile(r"\d+\s*Hz")


class MemoryViewTest(unittest.TestCase):
    """The map draws a walk it is given, and decodes nothing itself."""

    def test_the_view_states_no_address_of_its_own(self):
        # Addresses reach this view in the answer it asked for. One typed
        # into the module or the shell would be a claim about a machine
        # the page has not looked at.
        for path in (UI / "js" / "memory.mjs", UI / "index.html"):
            with self.subTest(file=path.name):
                self.assertFalse(HARD_ADDRESS.findall(path.read_text()))

    def test_the_view_decodes_no_descriptor(self):
        # A descriptor's bit layout has one source: the headers the
        # hypervisor compiles. A shift or a mask here would be a second
        # reading of it, drifting the first time a field moved.
        source = (UI / "js" / "memory.mjs").read_text()
        self.assertFalse(re.findall(r"[<>]{2}=?|&\s*0x|\bBigInt\b", source))

    def test_every_view_a_tab_names_is_built(self):
        # A tab pointing at nothing hides the current view and shows no
        # other; the switch is the one place both names have to agree.
        named = set(re.findall(r'data-view="(\w+)"', (UI / "index.html").read_text()))
        table = re.search(r"const VIEWS = \{(.*?)\n\};", (UI / "js" / "main.mjs").read_text(), re.S)
        self.assertIsNotNone(table, "view table not found")
        self.assertEqual(named, set(re.findall(r"^  (\w+):", table.group(1), re.M)))

    def test_a_board_that_was_hidden_measures_itself_again(self):
        # It draws wires from a measured box; measured at zero they land
        # on the origin. Folding already says so, and the view switch
        # leaves it in exactly the same state.
        self.assertRegex((UI / "js" / "board.mjs").read_text(), r"reveal\(\) \{\s+invalidate\(\)")
        self.assertRegex((UI / "js" / "main.mjs").read_text(), r"boardView\.reveal\(\)")
        css = (UI / "css" / "workbench.css").read_text()
        self.assertRegex(css, r"\.view\.folded\s*>\s*\.mmap\s*\{[^}]*display:\s*none")

    def test_the_map_marks_w_and_x_only_where_the_regime_forbids_it(self):
        # A guest's Stage 2 grants both on purpose — the guest's own
        # Stage 1 does the splitting — so marking every such row would
        # cry wolf on every run. The regime says which it is.
        source = (UI / "js" / "memory.mjs").read_text()
        self.assertRegex(source, r"wxn && row\.w && row\.x")
        self.assertRegex((UI / "css" / "workbench.css").read_text(), r"\.mperm\.wx")

    def test_the_map_states_the_wxn_result_even_when_it_is_none(self):
        # A check whose result nobody can see is indistinguishable from
        # one that never ran.
        self.assertRegex((UI / "js" / "memory.mjs").read_text(), r"if \(tree\.wxn\)")
        self.assertRegex((UI / "css" / "workbench.css").read_text(), r"\.mverdict")

    def test_a_short_map_says_so(self):
        # A walk that could not read a table returns fewer mappings.
        # Drawn plainly it reads as a machine that had fewer.
        source = (UI / "js" / "memory.mjs").read_text()
        self.assertIn("tree.unreadable", source)
        self.assertIn("tree.truncated", source)


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


def strip_js(source: str) -> str:
    """Blank out comments and string bodies, keeping every offset.

    Brace matching below has to skip a `{` that lives in a comment or a
    template literal. Replacing rather than deleting keeps the text the
    same length, so a reported position still points at real source.
    """
    out = list(source)
    at, end = 0, len(source)
    while at < end:
        char = source[at]
        if char == "/" and at + 1 < end and source[at + 1] in "/*":
            block = source[at + 1] == "*"
            close = source.find("*/", at + 2) if block else source.find("\n", at)
            stop = end if close < 0 else close + (2 if block else 0)
            for i in range(at, stop):
                if out[i] != "\n":
                    out[i] = " "
            at = stop
            continue
        if char in "\"'`":
            at += 1
            while at < end and source[at] != char:
                at += 2 if source[at] == "\\" else 1
                if at <= end:
                    continue
            # The literal's body is blanked; nested ${} is balanced anyway.
            at += 1
            continue
        at += 1
    text = "".join(out)
    for quote in "\"'`":
        text = re.sub(
            rf"{quote}(?:[^{quote}\\\n]|\\.)*{quote}",
            lambda hit: " " * len(hit.group(0)),
            text,
        )
    return text


def function_bodies(source: str) -> dict[str, str]:
    """Every `function name(...) {...}` body, by name."""
    blank = strip_js(source)
    bodies: dict[str, str] = {}
    for head in re.finditer(r"\bfunction\s+(\w+)\s*\(", blank):
        open_brace = blank.find("{", head.end())
        if open_brace < 0:
            continue
        depth, at = 0, open_brace
        while at < len(blank):
            if blank[at] == "{":
                depth += 1
            elif blank[at] == "}":
                depth -= 1
                if depth == 0:
                    break
            at += 1
        bodies[head.group(1)] = source[open_brace : at + 1]
    return bodies


# Anything that makes the browser reflow to answer.
LAYOUT_READ = re.compile(
    r"\b(?:getBoundingClientRect|offset(?:Width|Height|Top|Left)"
    r"|client(?:Width|Height|Top|Left)|scroll(?:Width|Height)|getComputedStyle)\b"
)
# Functions a snapshot can reach. Sizing and drag handlers are allowed to
# measure: they run on a gesture, not on a value.
DRAW_PATH = re.compile(r"^(?:render|paint|draw|flash|note|relink|residency|put)")


class BoardDrawPathTest(unittest.TestCase):
    """A snapshot must never make the browser reflow.

    The board separates measuring from writing: geometry is read in
    measure() and cached, and everything a topic can reach only writes
    text. One getBoundingClientRect on that path costs a forced layout
    per changed value — fourteen a batch before the split — and nothing
    on screen looks wrong when it happens.
    """

    def test_no_function_a_snapshot_reaches_reads_layout(self):
        bodies = function_bodies((UI / "js" / "board.mjs").read_text())
        self.assertIn("measure", bodies, "board measure() not found")
        self.assertRegex(bodies["measure"], LAYOUT_READ, "measure() stopped measuring")
        reached = [name for name in bodies if DRAW_PATH.match(name)]
        self.assertTrue(reached, "no draw-path functions found")
        for name in sorted(reached):
            with self.subTest(function=name):
                self.assertNotRegex(bodies[name], LAYOUT_READ)


class BoardAnchorTest(unittest.TestCase):
    """Endpoints are named, and the names are registered."""

    def test_every_endpoint_a_wire_uses_is_an_anchor_id(self):
        # A wire end is an anchor id, not a node, so the draw path never
        # touches the document. An id nothing registered measures to
        # undefined and the line quietly collapses onto the origin.
        source = (UI / "js" / "board.mjs").read_text()
        self.assertRegex(source, r"function anchor\(id, node\)")
        self.assertRegex(source, r"live\.anchors\.push\(\{ id, node \}\)")
        # The registry is reset with the skeleton, before anything fills it.
        self.assertRegex(source, r"live = \{ anchors: \[\] \}")
        bodies = function_bodies(source)
        self.assertNotRegex(bodies["measure"], r"live\.links\[|\.node\.getBounding")
        self.assertRegex(bodies["measure"], r"for \(const \{ id, node \} of live\.anchors")

    def test_the_board_registers_every_anchor_the_bridge_points_at(self):
        # The bridge names endpoints; the board has to have them. An id
        # nothing registered resolves to no box and the path is dropped
        # without a word — the exact silence the table exists to break.
        from novakit.services.workbench import paths

        source = (UI / "js" / "board.mjs").read_text()
        registered = set(re.findall(r'anchor\("([\w:]+)"', source))
        # Bands take their id from the layer's own title.
        registered |= {f"band:{title.lower()}" for title in re.findall(r'layer\("(\w+)"', source)}
        registered |= set(re.findall(r'chip\(band, "(\w+)"', source))
        # Strip segments are `<strip label>:<region kind>`, and the kinds
        # are exactly the ones the caption table knows — read from that
        # table alone, so a caption for something else cannot stand in
        # for a segment the board never actually anchors.
        labels = [label.lower() for label in re.findall(r'strip\(column, "(\w+)"', source)]
        table = re.search(r"const KIND_TEXT = \{(.*?)\n\};", source, re.S)
        self.assertIsNotNone(table, "segment caption table not found")
        kinds = re.findall(r"^  (\w+):", table.group(1), re.M)
        self.assertRegex(source, r"anchor\(`\$\{label\.toLowerCase\(\)\}:\$\{region\.kind\}`")
        registered |= {f"{label}:{kind}" for label in labels for kind in kinds}
        for name in (*paths.BANDS, *paths.CHIPS, *paths.SEGMENTS):
            with self.subTest(anchor=name):
                self.assertIn(name, registered)

    def test_every_region_kind_the_bridge_publishes_has_a_caption(self):
        """Same shape as the edge captions below: one vocabulary, two
        consumers. A kind added to the bridge and not here draws its
        internal id on the address strip, which is the UI leaking a wire
        format at the exact place a reader goes to read an address."""
        from novakit.services.workbench import hardware

        source = (UI / "js" / "board.mjs").read_text()
        table = re.search(r"const KIND_TEXT = \{(.*?)\n\};", source, re.S)
        self.assertIsNotNone(table, "region caption table not found")
        captioned = set(re.findall(r"^  (\w+):", table.group(1), re.M))
        published = {
            value
            for name, value in vars(hardware).items()
            if name.startswith("KIND_") and isinstance(value, str)
        }
        self.assertTrue(published)
        self.assertEqual(published - captioned, set())

    def test_the_physical_map_draws_the_trace_reservation(self):
        """It did not, and at 640 KiB that cost nothing. Sized by the
        stall it has to survive, the region is 16 to 32 MiB — and a map
        showing that as "unused" is wrong about the largest reservation
        on the board."""
        from novakit.services.workbench import hardware

        regions = hardware.board_map()["regions"]["pa"]
        traced = [region for region in regions if region["kind"] == hardware.KIND_TRACE]
        self.assertEqual(len(traced), 1)
        self.assertGreaterEqual(traced[0]["size"], 1 << 20)
        # And not as a shared page: this one is never in a guest's
        # Stage 2, which is the whole distinction the map has to keep.
        self.assertNotEqual(hardware.KIND_TRACE, hardware.KIND_SHARED)

    def test_every_path_the_bridge_publishes_has_a_caption(self):
        # An uncaptioned edge still draws; its tooltip just reads as an
        # internal id, which is the UI leaking its wire format.
        from novakit.services.workbench import paths

        source = (UI / "js" / "board.mjs").read_text()
        table = re.search(r"const EDGE_TEXT = \{(.*?)\n\};", source, re.S)
        self.assertIsNotNone(table, "edge caption table not found")
        captioned = set(re.findall(r"^  (\w+):", table.group(1), re.M))
        self.assertEqual({edge.id for edge in paths.EDGES}, captioned)


class SelectionTest(unittest.TestCase):
    """One cursor over the strip, moved three ways.

    A click, an arrow key and playback all push the same selection. A
    playback path of its own would mean the caption, the board focus and
    the grade badge exist twice — and two of anything that draws the
    same fact is how they come to disagree.
    """

    def setUp(self):
        self.source = (UI / "js" / "timeline.mjs").read_text()
        self.main = (UI / "js" / "main.mjs").read_text()

    def body(self, signature: str) -> str:
        found = re.search(rf"{signature} \{{(.*?)\n  \}}", self.source, re.S)
        self.assertIsNotNone(found, f"{signature} not found")
        return found.group(1)

    def test_the_cursor_is_a_record_and_its_position_is_derived(self):
        """The list a selection came from is rebuilt from the window on
        demand — a resize, a drag or an arriving batch changes it. An
        index kept across that names a different record than the caption
        said, and the paint, which reads the cursor without rebuilding
        anything, would draw the line where nobody pointed.

        "The next one" is still answerable: find the record in the
        rebuilt list and move. The position was only ever stored by
        accident.
        """
        self.assertRegex(self.source, r"let picked = null;")
        self.assertRegex(self.source, r"const positionOf = \(rows\) =>")
        # Nothing keeps a position across calls.
        self.assertNotIn("chosenAt", self.source)
        # And the paint draws from the record, not from an index.
        self.assertIn("if (!picked) return;", self.body(r"function cursorLine\(window_, at, colours\)"))

    def test_one_rule_says_whether_two_records_are_the_same(self):
        """Two yields of visible() are different objects for one record,
        so identity cannot answer it — and a second comparison that
        forgot the ring would confuse two cores sharing a timestamp."""
        self.assertRegex(self.source, r"const same = \(a, b\) =>[\s\S]{0,160}a\.cpu === b\.cpu")
        click = re.search(r'addEventListener\("pointerup".*?\n  \}\);', self.source, re.S)
        self.assertIn("same(row, hit)", click.group(0))

    def test_playback_moves_the_same_cursor_a_click_moves(self):
        # The tick calls step(), which calls select() — the one place a
        # selection is announced.
        play = self.body(r"function play\(speed = 1\)")
        self.assertIn("step(+1)", play)
        self.assertNotIn("onSelect(", play)
        click = re.search(r'addEventListener\("pointerup".*?\n  \}\);', self.source, re.S)
        self.assertIsNotNone(click)
        self.assertRegex(click.group(0), r"select\(index, rows\)")
        self.assertNotIn('onSelect({ kind: "mark"', click.group(0))

    def test_playback_compresses_idle_time_and_never_the_order(self):
        """Real time is either a blur or a wait; even spacing lies about
        the timing. Between the bounds the delay tracks the real gap,
        and the printed delta is always the real one."""
        play = self.body(r"function play\(speed = 1\)")
        self.assertIn("Math.min(STEP_MAX_MS, Math.max(STEP_MIN_MS", play)
        # The delay the player chose never becomes the delta a reader
        # reads: playback computes no `dt` at all.
        self.assertNotIn("dt", play)
        # That comes from the record timestamps, where the selection is
        # announced, and reaches the caption unchanged.
        self.assertRegex(self.body(r"function select\(index, rows = laid\(\)\)"),
                         r"dt: at > 0 \? micros\(record\.ts - rows\[at - 1\]\.ts\)")
        self.assertIn("Δt ${choice.dt}us", self.main)

    def test_a_new_set_of_records_drops_the_selection(self):
        """A record that no longer exists is not a cursor, and keeping
        it would draw a line at a time nothing is on."""
        self.assertRegex(self.source, r"function dropSelection\(\)")
        self.assertIn("dropSelection()", self.body(r"function reset\(\)"))


class PathTourTest(unittest.TestCase):
    """Walking a path is the recorded order, not a script.

    The earlier design was a static chain of numbered hops per path. A
    script can be wrong about the machine and stay wrong quietly; a
    recording cannot be wrong about itself. This is the composition
    that replaced it: a filtered window the bridge already answers, and
    the selection cursor that already walks whatever came back.
    """

    def setUp(self):
        self.board = (UI / "js" / "board.mjs").read_text()
        self.timeline = (UI / "js" / "timeline.mjs").read_text()
        self.main = (UI / "js" / "main.mjs").read_text()

    def test_a_path_can_be_aimed_at_without_widening_it(self):
        """The drawn width is what says how well a path is observed, so
        the click target is a companion rather than a thicker line."""
        self.assertIn('hit.dataset.edge = spec.id', self.board)
        css = (UI / "css" / "workbench.css").read_text()
        self.assertRegex(css, r"\.edge-hit\{[^}]*stroke:\s*transparent")
        self.assertRegex(css, r"\.edge-hit\{[^}]*pointer-events:\s*stroke")
        # Shown and hidden with the path, or it is a target for
        # something that is not on screen.
        show = re.search(r"function showEdge\(edge, on\) \{(.*?)\n  \}", self.board, re.S)
        self.assertIn("edge.hit.style.display", show.group(1))

    def test_the_board_says_which_path_and_nothing_about_the_trace(self):
        click = re.search(
            r'wires\.addEventListener\("click".*?\n  \}\);', self.board, re.S)
        self.assertIsNotNone(click, "no path click handler")
        self.assertIn("onTour(id)", click.group(0))
        # The recorded order lives in the strip; a second reading of it
        # here would be a second answer.
        self.assertNotIn("window", click.group(0))

    def test_the_moments_that_light_a_path_come_from_the_catalogue(self):
        """A per-path list of event ids typed into the client would be a
        second copy of what the bridge already publishes."""
        self.assertRegex(
            self.main, r"catalogue\.filter\(\(stop\) => stop\.edge === edge\)")

    def test_the_tour_is_a_filtered_window_walked_by_the_one_cursor(self):
        tour = re.search(r"function tour\(eventIds, label\) \{(.*?)\n  \}", self.timeline, re.S)
        self.assertIsNotNone(tour, "tour not found")
        self.assertIn("events: eventIds", tour.group(1))
        # No records are drawn here and no selection is announced here;
        # the window answer starts the same cursor everything else uses.
        self.assertNotIn("onSelect(", tour.group(1))
        self.assertRegex(self.timeline, r"if \(touring\) \{[\s\S]{0,200}select\(0\);")


class ReplayViewTest(unittest.TestCase):
    """A replay is real and was real, and a reader has to know which."""

    def setUp(self):
        self.main = (UI / "js" / "main.mjs").read_text()

    def test_the_phase_is_named_rather_than_shown_as_idle(self):
        self.assertRegex(self.main, r"replay: \{ text:")

    def test_a_recorded_lifecycle_is_history_not_a_transition(self):
        """A recording replays its own 'running' and 'exited'. Obeyed,
        they would put a live badge on a screen with no machine."""
        self.assertRegex(
            self.main, r'function setPhase\([\s\S]{0,80}if \(replaying && phase !== "replay"\)')

    def test_launching_is_refused_from_one_place(self):
        """Seven call sites re-armed the button directly. A replay has
        to refuse all of them, which is one rule about the session and
        not seven about them."""
        self.assertNotIn("runButton.disabled = false", self.main)
        self.assertRegex(self.main, r"function armRun\(on\) \{\n  runButton\.disabled = replaying")


class StepperTest(unittest.TestCase):
    """The controls that stop the machine at an event."""

    def setUp(self):
        self.html = (UI / "index.html").read_text()
        self.main = (UI / "js" / "main.mjs").read_text()
        self.css = (UI / "css" / "workbench.css").read_text()

    def test_every_element_the_script_reaches_for_exists(self):
        """A `ref()` that finds nothing throws on the first click, and
        the header looks fine until then."""
        ids = set(re.findall(r'\bid="([\w-]+)"', self.html))
        for name in re.findall(r'\bref\("([\w-]+)"\)', self.main):
            with self.subTest(id=name):
                self.assertIn(name, ids)

    def test_the_stop_choices_come_from_the_bridge(self):
        """The catalogue lives beside the firmware symbols it names. A
        list typed here would be a second copy, free to disagree."""
        self.assertIn("setStops(topo.stops)", self.main)
        for event in ("post_spi_tracked", "drain_eois", "handle_lower_sync"):
            self.assertNotIn(event, self.main)
        self.assertNotIn("vgic.bind", self.html)

    def test_the_grade_a_stop_earns_is_styled(self):
        from novakit.services.workbench import paths

        self.assertIn(f".edge.{paths.GRADE_DIRECT}{{", self.css)
        self.assertIn(f'"{paths.GRADE_DIRECT}"', (UI / "js" / "board.mjs").read_text())

    def test_the_legend_names_every_grade_the_bridge_can_publish(self):
        """A path drawn in a style the legend does not explain is a
        stroke the reader has no way to read."""
        for swatch in ("direct", "poll", "none"):
            with self.subTest(swatch=swatch):
                self.assertIn(f'class="{swatch}"', self.html)

    def test_a_stop_reaches_the_board_as_measurement(self):
        """The stop carries the event's own argument registers, so the
        board states them rather than describing how closely it looked."""
        self.assertIn("boardView.stopped(ts, data)", self.main)
        board = (UI / "js" / "board.mjs").read_text()
        self.assertRegex(board, r"function stopped\(ts, data\)")
        self.assertIn("stopped,", board)


class FocusLayerTest(unittest.TestCase):
    """Two reasons to hide a row, kept apart."""

    def test_the_board_narrowing_is_not_the_readers_muting(self):
        # Folded into one set, clearing the focus would un-mute chips the
        # reader had switched off themselves — their state lost silently,
        # looking like the log misbehaving rather than the board.
        source = (UI / "js" / "events.mjs").read_text()
        self.assertRegex(source, r"const muted = new Set\(\)")
        self.assertRegex(source, r"let narrowed = null")
        self.assertRegex(
            source,
            r"muted\.has\(name\)\s*\|\|\s*\(narrowed !== null && !narrowed\.has\(name\)\)",
            "hiding no longer consults both layers",
        )
        # narrow() must never touch the reader's set.
        body = function_bodies(source)["narrow"]
        self.assertNotIn("muted", body, "narrowing writes the reader's own filter")

    def test_focus_derives_its_badges_from_the_published_paths(self):
        # A hand-written block-to-subsystem table would be a second copy
        # of what the path table already says, drifting the moment an
        # edge is added.
        source = (UI / "js" / "board.mjs").read_text()
        body = function_bodies(source)["badgesAt"]
        self.assertIn("live.edges", body)
        self.assertIn("edge.badges", body)


class ConsoleReachTest(unittest.TestCase):
    """One event, two readers, one parser."""

    def test_a_classified_event_reaches_the_board_as_well_as_the_log(self):
        # A path whose only evidence is a console line stays dark unless
        # the board is handed the event, and nothing about the screen
        # says why.
        main = (UI / "js" / "main.mjs").read_text()
        block = re.search(r'case "ev":(.*?)break;', main, re.S)
        self.assertIsNotNone(block, "no console-event case in the frame dispatch")
        self.assertIn("events.addEvent", block.group(1))
        self.assertIn("boardView.note", block.group(1))

    def test_the_board_does_not_read_console_text_itself(self):
        # Interpreting the firmware's log is the bridge's, and a contract
        # test already ties every rule there to a real firmware string. A
        # pattern here would be a second parser outside that contract,
        # drifting on its own. The board routes the badge and shows the
        # message; it never asks what the message says.
        source = (UI / "js" / "board.mjs").read_text()
        blank = strip_js(source)
        for applied in re.findall(r"\.(match|matchAll|exec|test|search)\s*\(", blank):
            self.fail(f"board.mjs applies a pattern with .{applied}()")
        body = function_bodies(source)["note"]
        self.assertIn("byBadge", body, "the console path stopped routing by badge")
        for literal in re.findall(r"/(?![/*])(?:[^/\\\n]|\\.)+/[gimsuy]*", strip_js(body)):
            self.fail(f"the console path carries a regular expression: {literal}")


PULSE_RULE = re.compile(r"\.edge\.([\w-]+)\s*\{\s*animation:\s*([\w-]+)")
KEYFRAMES = re.compile(r"@keyframes\s+([\w-]+)")


class PulseRestartTest(unittest.TestCase):
    """A second piece of evidence has to show.

    Re-adding a class an element already has does not restart a CSS
    animation, so the board alternates between two. The catch is that an
    animation is identified by its *name*: two classes resolving to one
    set of keyframes leave the running animation untouched, and the
    second pulse is invisible. Nothing throws — the board just quietly
    stops reporting, which is why this is held here rather than left to
    a browser nobody runs in CI.
    """

    def rules(self) -> dict[str, str]:
        css = (UI / "css" / "workbench.css").read_text()
        return dict(PULSE_RULE.findall(css))

    def test_the_js_and_css_agree_on_the_class_names(self):
        source = (UI / "js" / "board.mjs").read_text()
        listed = re.search(r"const PULSE = \[(.*?)\];", source, re.S)
        self.assertIsNotNone(listed, "pulse class list not found")
        names = set(re.findall(r'"([\w-]+)"', listed.group(1)))
        self.assertTrue(names)
        self.assertEqual(names, set(self.rules()), "a pulse class nothing styles, or the reverse")

    def test_each_pulse_class_names_its_own_animation(self):
        animations = list(self.rules().values())
        self.assertTrue(animations)
        self.assertEqual(
            len(animations), len(set(animations)),
            f"two pulse classes share an animation name, so one cannot restart: {animations}",
        )

    def test_every_named_animation_exists(self):
        css = (UI / "css" / "workbench.css").read_text()
        declared = set(KEYFRAMES.findall(css))
        for klass, animation in self.rules().items():
            with self.subTest(pulse=klass):
                self.assertIn(animation, declared)

    def test_the_pulse_respects_reduced_motion(self):
        css = (UI / "css" / "workbench.css").read_text()
        block = re.search(r"@media \(prefers-reduced-motion:\s*reduce\)\s*\{(.*?)\n\}", css, re.S)
        self.assertIsNotNone(block, "no reduced-motion rule for the pulse")
        for klass in self.rules():
            with self.subTest(pulse=klass):
                self.assertIn(f".edge.{klass}", block.group(1))


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
