"""The path table is only worth having if it cannot drift.

Every edge names a topic the manifest publishes, a badge the taxonomy
defines, and two endpoints the board actually draws. Each of those is a
join between two files, and a join nobody checks is where an edge goes
quietly dark: the line keeps being drawn, it just stops ever lighting.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from novakit.services.workbench import events, hardware, paths  # noqa: E402
from novakit.services.workbench.observations import OBSERVATIONS  # noqa: E402
from novakit.services.workbench.taxonomy import Badge  # noqa: E402

GRADES = {paths.GRADE_CONSOLE, paths.GRADE_POLL, paths.GRADE_NONE}


class EdgeTableTest(unittest.TestCase):
    def test_every_polled_edge_names_a_published_topic(self):
        published = {obs.topic for obs in OBSERVATIONS}
        for edge in paths.EDGES:
            with self.subTest(edge=edge.id):
                if edge.grade == paths.GRADE_POLL:
                    self.assertIn(edge.topic, published, "polls a topic nobody publishes")
                else:
                    # A console or unwatched edge claiming a topic would
                    # be stroked as unsampled while sampling something.
                    self.assertEqual(edge.topic, "", "not a polled edge, but names a topic")

    def test_every_badge_is_taxonomy(self):
        for edge in paths.EDGES:
            for badge in edge.badges:
                with self.subTest(edge=edge.id, badge=badge):
                    self.assertIsInstance(badge, Badge)

    def test_a_console_edge_has_something_to_listen_for(self):
        # Its grade promises timing-exact evidence. With no badge behind
        # it, nothing ever arrives and the promise is empty.
        for edge in paths.EDGES:
            if edge.grade == paths.GRADE_CONSOLE:
                with self.subTest(edge=edge.id):
                    self.assertTrue(edge.badges)

    def test_an_unwatched_edge_admits_it(self):
        for edge in paths.EDGES:
            if edge.grade == paths.GRADE_NONE:
                with self.subTest(edge=edge.id):
                    self.assertFalse(edge.topic or edge.badges, "watched, but graded as not")

    def test_grades_and_ids_are_sound(self):
        ids = [edge.id for edge in paths.EDGES]
        self.assertEqual(len(ids), len(set(ids)), "duplicate edge id")
        for edge in paths.EDGES:
            with self.subTest(edge=edge.id):
                self.assertIn(edge.grade, GRADES)
                paired = bool(edge.pair)
                self.assertEqual(paired, not (edge.source and edge.target),
                                 "an edge either names both ends or expands over a group")


class PublishedEdgeTest(unittest.TestCase):
    """What board_map() actually ships."""

    def setUp(self):
        self.board = hardware.board_map()
        self.edges = self.board["edges"]

    def test_every_endpoint_is_a_block_the_board_draws(self):
        # An endpoint nothing draws measures to nothing, and the path is
        # silently dropped — the failure mode this table exists to avoid.
        known = {block["id"] for block in self.board["blocks"]}
        known |= set(paths.BANDS) | set(paths.CHIPS) | set(paths.SEGMENTS)
        known |= {f"core{cpu}" for cpu in range(self.board["cpus"])}
        for edge in self.edges:
            with self.subTest(edge=edge["id"]):
                self.assertIn(edge["from"], known)
                self.assertIn(edge["to"], known)

    def test_a_group_expands_to_one_edge_per_adjacent_pair(self):
        cores = [edge for edge in self.edges if edge["id"].startswith("cross")]
        self.assertEqual(len(cores), max(0, self.board["cpus"] - 1))
        for edge in cores:
            self.assertNotEqual(edge["from"], edge["to"], "a path from a core to itself")

    def test_a_board_without_a_block_simply_has_fewer_paths(self):
        # Filtering is what lets one table describe every board. Drop a
        # block and the paths into it must go, not draw to nowhere.
        thinned = paths.edges(self.board["cpus"], [])
        named = {edge["id"] for edge in thinned}
        self.assertNotIn("dma", named, "kept a path into a block this board has not got")
        self.assertIn("trap", named, "dropped a path between two bands every board has")

    def test_ids_stay_unique_after_expansion(self):
        ids = [edge["id"] for edge in self.edges]
        self.assertEqual(len(ids), len(set(ids)))


class CapabilityGradeTest(unittest.TestCase):
    """A grade describes what is watching, so it has to come from the
    run and not from a table in the source.

    The catalogue names the same moments whatever was built. Grading
    from it painted a stripped image exactly as certain as a debug one —
    the overstatement the whole grade scheme exists to prevent, made by
    the grade calculation itself.
    """

    def test_an_image_with_nothing_to_watch_with_claims_nothing(self):
        self.assertEqual(events.observable(None, tracing=False), set())

    def test_the_rings_alone_are_enough(self):
        """The firmware emits whether or not its names survived, so a
        stripped image with tracing is still direct evidence."""
        witnessed = events.observable(None, tracing=True)
        self.assertIn(paths.EDGE_POST, witnessed)
        self.assertIn("phys", witnessed)

    def test_a_fault_names_no_path(self):
        """smmu.fault is catalogued and deliberately has no edge: what a
        failed translation proves is that one failed, never anything
        about the path a working one takes."""
        self.assertEqual(events.BY_ID["smmu.fault"].edge, "")

    def test_the_device_lanes_are_watched_from_the_normal_path(self):
        """Both were grey because nothing pointed at them. What points
        now is a transaction leaving the device and a stream's route to
        memory being established — moments that happen when nothing is
        wrong, which is what an edge's grade is a claim about."""
        by_edge = {}
        for event in events.EVENTS:
            by_edge.setdefault(event.edge, set()).add(event.id)
        self.assertEqual(by_edge[paths.EDGE_DMA], {"dma.start"})
        self.assertEqual(by_edge[paths.EDGE_WALK], {"smmu.attach"})
        witnessed = events.observable(None, tracing=True)
        self.assertLessEqual({paths.EDGE_DMA, paths.EDGE_WALK}, witnessed)

    def test_losing_the_capability_demotes_the_path(self):
        watched = hardware.board_map(direct=events.observable(None, tracing=True))
        blind = hardware.board_map(direct=())
        graded = {edge["id"]: edge["grade"] for edge in watched["edges"]}
        ungraded = {edge["id"]: edge["grade"] for edge in blind["edges"]}
        self.assertEqual(graded["phys"], paths.GRADE_DIRECT)
        self.assertEqual(ungraded["phys"], paths.GRADE_CONSOLE)
        # The two maps describe one board: only the certainty differs.
        self.assertEqual(set(graded), set(ungraded))


if __name__ == "__main__":
    unittest.main()
