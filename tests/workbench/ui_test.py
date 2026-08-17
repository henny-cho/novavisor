"""Structural contracts of the build-step-free workbench UI.

Without a bundler, a broken import path or a token drift is invisible
until a browser opens the page; these checks make both fail in CI.

Two other tools hold what this file used to. What a module is forbidden
to *say* is in `web/eslint.config.mjs`: a parser tells a literal from a
comment and an operator from the same characters inside a string, which
a regular expression over the source text cannot. What a module *does*
is in `web/test/`, where node runs it against a small DOM — a cursor
that steps, a table that refuses a cell with no provenance, a panel
that declines to rebuild.

What is left is the agreement between files that no single language's
tools can see: markup to stylesheet to module, and the vocabulary the
bridge publishes to the UI that has to name it.
"""

from __future__ import annotations

import re
import unittest

from tests import REPO

UI = REPO / "web" / "workbench"

HTML_REFERENCE = re.compile(r'(?:src|href)="([^"]+)"')
MODULE_IMPORT = re.compile(r'(?:import|from)\s+"(\./[^"]+)"')
CUSTOM_PROPERTY = re.compile(r"(--[\w-]+)\s*:\s*([^;]+);")
PROTOCOL_CONST = re.compile(r"PROTOCOL_VERSION\s*=\s*(\d+)")


class ProtocolVersionTest(unittest.TestCase):
    """The version stamped on every downlink envelope; the client checks
    it on every frame and drops the whole stream without a word on a
    mismatch, so the two copies drifting apart is invisible until a
    browser opens the page."""

    def test_the_client_expects_what_the_bridge_stamps(self):
        from novakit.services.workbench import protocol

        client = PROTOCOL_CONST.search((UI / "js" / "net.mjs").read_text())
        self.assertIsNotNone(client, "net.mjs PROTOCOL_VERSION not found")
        self.assertEqual(int(client.group(1)), protocol.PROTOCOL_VERSION)


class FailureVisibilityTest(unittest.TestCase):
    def setUp(self):
        self.main = (UI / "js" / "main.mjs").read_text()
        found = re.search(r"function onLife\(.*?\n}\n\nfunction onFrame", self.main, re.S)
        self.assertIsNotNone(found, "life handler not found")
        self.life = found.group(0)

    def life_case(self, phase: str) -> str:
        found = re.search(
            rf'case "{re.escape(phase)}":(.*?)(?=\n    case |\n    default:)',
            self.life,
            re.S,
        )
        self.assertIsNotNone(found, f"life phase is not visible: {phase}")
        return found.group(1)

    def test_critical_failures_keep_their_error(self):
        for phase in ("task-failed", "snapshot-unavailable"):
            with self.subTest(phase=phase):
                block = self.life_case(phase)
                self.assertIn("data.error", block)
                self.assertIn('severity: "CRIT"', block)

    def test_warnings_name_their_target(self):
        expected = {"stop-failed": "data.target", "guests-differ": "data.guests"}
        for phase, field in expected.items():
            with self.subTest(phase=phase):
                block = self.life_case(phase)
                self.assertIn(field, block)
                self.assertIn('severity: "WARN"', block)

    def test_unknown_lifecycle_details_are_not_dimmed_away(self):
        fallback = re.search(r"\n    default:(.*?)\n  }\n}", self.life, re.S)
        self.assertIsNotNone(fallback, "life fallback not found")
        self.assertIn("lifeDetail(data)", fallback.group(1))
        self.assertNotIn("dim: true", fallback.group(1))

    def test_every_view_tab_is_live(self):
        tabs = re.findall(r'<button class="vtab".*?</button>', (UI / "index.html").read_text(), re.S)
        self.assertTrue(tabs)
        self.assertTrue(all("disabled" not in tab for tab in tabs))


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


VIEW_HEADER = re.compile(r'<div class="view-h">(.*?)</div>\s*<div class="board"', re.S)
# `"topic": ["section", ...]`
PAINTS = re.compile(r'"([\w.]+)":\s*\[([^\]]*)\]')


class MemoryViewTest(unittest.TestCase):
    """What the map is joined to: the tab that switches to it, and the
    manifest it reads."""

    def test_every_view_a_tab_names_is_built(self):
        # A tab pointing at nothing hides the current view and shows no
        # other; the switch is the one place both names have to agree.
        named = set(re.findall(r'data-view="(\w+)"', (UI / "index.html").read_text()))
        table = re.search(r"const VIEWS = \{(.*?)\n\};", (UI / "js" / "main.mjs").read_text(), re.S)
        self.assertIsNotNone(table, "view table not found")
        self.assertEqual(named, set(re.findall(r"^  (\w+):", table.group(1), re.M)))

    def test_the_map_reads_only_published_topics(self):
        # The map is static under a run; what a stream is allowed to do
        # is not, and only that is polled. A topic the manifest stopped
        # publishing would leave the stream strip silently empty.
        from novakit.services.workbench.observations import OBSERVATIONS

        source = (UI / "js" / "memory.mjs").read_text()
        table = re.search(r"const TOPICS = new Set\(\[(.*?)\]\);", source, re.S)
        self.assertIsNotNone(table, "memory topic table not found")
        wanted = set(re.findall(r'"([\w.]+)"', table.group(1)))
        self.assertTrue(wanted)
        self.assertLessEqual(wanted, {obs.topic for obs in OBSERVATIONS})


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


class BoardAnchorTest(unittest.TestCase):
    """Endpoints are named, and the names are registered."""

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

    def grades(self) -> set[str]:
        """Every grade the bridge can put on an edge, asked of the bridge.

        A list spelled out here would be a copy of that vocabulary, and
        it agrees with it only until a grade is added.
        """
        from novakit.services.workbench import paths

        found = {
            value
            for name, value in vars(paths).items()
            if name.startswith("GRADE_") and isinstance(value, str)
        }
        self.assertTrue(found)
        return found

    def test_every_grade_a_stop_can_earn_is_styled(self):
        # The board writes the bridge's own word for the grade straight
        # into the class, so a grade nothing styles draws in the base
        # stroke and says exactly what the path beside it says.
        for grade in sorted(self.grades()):
            with self.subTest(grade=grade):
                self.assertIn(f".edge.{grade}{{", self.css)

    def test_the_legend_names_every_grade_the_bridge_can_publish(self):
        """A path drawn in a style the legend does not explain is a
        stroke the reader has no way to read."""
        legend = re.search(r'<div class="legend".*?</div>', self.html, re.S)
        self.assertIsNotNone(legend, "legend not found")
        for grade in sorted(self.grades()):
            with self.subTest(swatch=grade):
                self.assertIn(f'class="{grade}"', legend.group(0))
                # A swatch with no rule of its own is the base stroke,
                # which is another grade's swatch.
                self.assertIn(f".lg i.{grade}", self.css)

    def test_a_stop_reaches_the_board_as_measurement(self):
        """The stop carries the event's own argument registers, so the
        board states them rather than describing how closely it looked."""
        self.assertIn("boardView.stopped(ts, data)", self.main)
        board = (UI / "js" / "board.mjs").read_text()
        self.assertRegex(board, r"function stopped\(ts, data\)")
        self.assertIn("stopped,", board)


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


class PanelStyleTest(unittest.TestCase):
    """What a highlighted cell needs from the stylesheet.

    Which cells light up is settled in `web/test/panels.test.mjs`, by
    rendering a reading beside the mask that came with it. The class it
    lands on has to mean something, and only the stylesheet says so.
    """

    def test_the_moved_cell_has_a_style_to_be_seen_by(self):
        css = (UI / "css" / "workbench.css").read_text()
        self.assertRegex(css, r"\.ptable td\.moved")


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


class DriveViewTest(unittest.TestCase):
    """What the one panel that acts on the machine is joined to.

    What it builds, what it sends and when it declines to rebuild are
    in `web/test/drive.test.mjs`, which presses the controls. Left here
    are the two joins node cannot see from inside the page: the
    firmware's opcode vocabulary, and the stylesheet.
    """

    def test_the_panel_holds_no_opcode_at_all(self):
        # The controls are built from the rows the machine published, so
        # an opcode spelled here would be a second list to go stale
        # against — including in the prose, which now rides on the rows.
        source = (UI / "js" / "drive.mjs").read_text()
        self.assertEqual(set(re.findall(r'issue\("(\w+)"', source)), set())
        self.assertNotRegex(source, r"const (LABEL|ACTION)\s*=")

    def test_the_verdict_reaches_the_panel_and_can_be_read_as_one(self):
        # EL2 answers with a trace record like everything else it says;
        # unrouted it never arrives at all, and a refusal drawn like an
        # acceptance is a verdict nobody can read.
        self.assertRegex((UI / "js" / "main.mjs").read_text(), r"drive\.answered\(data\.command\)")
        self.assertRegex((UI / "css" / "workbench.css").read_text(), r"\.dnote\.bad")
