"""Answering for part of the history, at the resolution asked for.

The ring's unique product is order, and the summary throws it away. A
window request is how a reader gets it back — and the shape of the
answer is decided by one number, the columns the caller can draw, so
there is no second cap to pick and nothing to truncate.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from novakit.services.workbench import events, trace  # noqa: E402
from novakit.services.workbench.session import Phase  # noqa: E402

BIND = events.BY_ID["vgic.bind"].code
TRAP = events.BY_ID["trap"].code


def records(stamps, code: int = TRAP) -> list[trace.Record]:
    return [trace.Record(ts=ts, code=code, cpu=0, a=ts, b=ts, c=ts) for ts in stamps]


class HistogramTest(unittest.TestCase):
    def test_every_record_lands_in_a_column(self):
        """A wide window is answered by summarising all of it. Returning
        the first N and a count of the rest is honest arithmetic about a
        question the reader did not ask."""
        found = records(range(0, 1000))
        hist = trace.histogram(found, 0, 999, 10)
        self.assertEqual(sum(hist["trap"]), 1000)
        self.assertEqual(len(hist["trap"]), 10)
        self.assertEqual(hist["trap"], [100] * 10)

    def test_the_last_instant_belongs_to_the_last_column(self):
        hist = trace.histogram(records([0, 99]), 0, 99, 10)
        self.assertEqual(hist["trap"][0], 1)
        self.assertEqual(hist["trap"][-1], 1)

    def test_lanes_are_events_not_paths(self):
        """Three events share the `post` edge; a lane per path would sum
        them into one column that no longer says which fired."""
        found = records([1], BIND) + records([2], events.BY_ID["vgic.spi"].code)
        hist = trace.histogram(found, 0, 10, 4)
        self.assertEqual(set(hist), {"vgic.bind", "vgic.spi"})

    def test_an_unknown_code_is_skipped_rather_than_charted(self):
        self.assertEqual(trace.histogram(records([1], 999), 0, 10, 4), {})

    def test_a_single_instant_window_does_not_divide_by_zero(self):
        hist = trace.histogram(records([5, 5]), 5, 5, 4)
        self.assertEqual(sum(hist["trap"]), 2)


class ColumnTest(unittest.TestCase):
    def test_timestamps_are_relative_to_the_window(self):
        """Small numbers, and well inside what a JSON number carries
        exactly — which a raw 64-bit counter is not guaranteed to be."""
        cols = trace.columns(records([1000, 1005]), 1000)
        self.assertEqual(cols["ts"], [0, 5])

    def test_columns_are_parallel_and_complete(self):
        cols = trace.columns(records([1, 2, 3]), 0)
        self.assertEqual({len(values) for values in cols.values()}, {3})
        self.assertEqual(set(cols), {"ts", "code", "cpu", "a", "b", "c"})

    def test_the_catalogue_carries_the_code_the_columns_use(self):
        """Otherwise a column-encoded record reaches the UI as a number
        with nothing to look it up in."""
        for entry in events.catalogue():
            with self.subTest(event=entry["id"]):
                self.assertEqual(entry["code"], events.BY_ID[entry["id"]].code)


class WindowRequestTest(unittest.IsolatedAsyncioTestCase):
    """The uplink, against a bridge whose history is seeded directly."""

    def bridge(self, stamps=range(0, 100)):
        from novakit.services.workbench.server import Bridge

        directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: directory.rmdir())
        bridge = Bridge(ui_root=directory, trace_history=4096)
        bridge.session.phase = Phase.RUNNING
        bridge._history.append(records(stamps))
        bridge.store.drain()
        return bridge

    def answer(self, bridge, **request) -> dict:
        bridge._handle_uplink(json.dumps({"topic": "trace", "data": {"op": "window", **request}}))
        frames = bridge.store.drain()
        replies = [f for f in frames if f["topic"] == "trace" and f["kind"] == "snapshot"]
        self.assertEqual(len(replies), 1, f"expected one reply, got {frames}")
        return replies[0]["data"]

    def rejection(self, bridge, **request) -> str:
        bridge._handle_uplink(json.dumps({"topic": "trace", "data": {"op": "window", **request}}))
        reasons = [
            f["data"]["reason"]
            for f in bridge.store.drain()
            if f["data"].get("phase") == "uplink-rejected"
        ]
        self.assertEqual(len(reasons), 1)
        return reasons[0]

    async def test_a_narrow_window_carries_the_records_and_only_those(self):
        """Once the records fit they are sent, and a histogram of them
        is a loop the client already has the data for. Sending both put
        1200 mostly-zero buckets beside four marks."""
        data = self.answer(self.bridge(), **{"from": 10, "to": 19, "buckets": 100})
        self.assertEqual(data["window"]["n"], 10)
        self.assertEqual(data["cols"]["ts"], list(range(10)))
        self.assertNotIn("hist", data)

    async def test_a_wide_window_carries_density_and_no_marks(self):
        """More points than pixels is a density. The reader narrows the
        window to see marks, which is what dragging one is for."""
        data = self.answer(self.bridge(), **{"from": 0, "to": 99, "buckets": 10})
        self.assertNotIn("cols", data)
        self.assertEqual(sum(data["hist"]["trap"]), 100, "a wide window still counts all of it")

    async def test_the_answer_states_the_horizon_it_was_taken_from(self):
        data = self.answer(self.bridge(), **{"from": 0, "to": 99})
        self.assertEqual(data["span"]["n"], 100)
        self.assertFalse(data["span"]["full"])

    async def test_an_event_filter_narrows_what_comes_back(self):
        bridge = self.bridge()
        bridge._history.append(records([200], BIND))
        bridge.store.drain()
        data = self.answer(bridge, **{"from": 0, "to": 500, "events": ["vgic.bind"]})
        self.assertEqual(data["window"]["n"], 1)
        self.assertEqual(data["cols"]["code"], [BIND])

    async def test_a_filter_applies_to_the_density_too(self):
        bridge = self.bridge()
        bridge._history.append(records([200], BIND))
        bridge.store.drain()
        data = self.answer(bridge, **{"from": 0, "to": 500, "events": ["trap"], "buckets": 4})
        self.assertEqual(set(data["hist"]), {"trap"})
        self.assertEqual(sum(data["hist"]["trap"]), 100)

    async def test_an_absurd_resolution_is_refused_not_quietly_reduced(self):
        """The response arrays are as long as this number, and a caller
        that asked for a million columns has misunderstood something."""
        self.assertIn("buckets", self.rejection(self.bridge(), buckets=10**6))

    async def test_a_backwards_window_is_refused(self):
        self.assertIn("ends before", self.rejection(self.bridge(), **{"from": 50, "to": 10}))

    async def test_an_unknown_op_is_refused_rather_than_guessed(self):
        bridge = self.bridge()
        bridge._handle_uplink('{"topic":"trace","data":{"op":"everything"}}')
        reasons = [
            f["data"].get("reason")
            for f in bridge.store.drain()
            if f["data"].get("phase") == "uplink-rejected"
        ]
        self.assertEqual(len(reasons), 1)
        self.assertIn("unknown op", reasons[0])

    async def test_asking_before_anything_ran_answers_empty(self):
        """A client may ask at any time; an empty history is an answer,
        not a fault."""
        bridge = self.bridge(stamps=[])
        data = self.answer(bridge, **{"from": 0, "to": 10})
        self.assertEqual(data["window"]["n"], 0)
        self.assertEqual(data["cols"]["ts"], [])


if __name__ == "__main__":
    unittest.main()
