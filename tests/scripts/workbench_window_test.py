"""Answering for part of the history, at the resolution asked for.

The ring's unique product is order, and the summary throws it away. A
window request is how a reader gets it back — and the shape of the
answer is decided by one number, the columns the caller can draw, so
there is no second cap to pick and nothing to truncate.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from novakit.services.workbench import client, events, trace  # noqa: E402

BIND = events.BY_ID["vgic.bind"].code
TRAP = events.BY_ID["trap"].code


def records(stamps, code: int = TRAP) -> list[trace.Record]:
    return [trace.Record(ts=ts, code=code, cpu=0, a=ts, b=ts, c=ts) for ts in stamps]


def packed(records_: list[trace.Record]) -> bytes:
    out = bytearray(len(records_) * trace.REC_SIZE)
    for index, record in enumerate(records_):
        trace.pack_into(out, index * trace.REC_SIZE, record)
    return bytes(out)


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
        _, _, cols = trace.window(packed(records([1000, 1005])), 1000, 1005, 2, set())
        self.assertIsNotNone(cols)
        self.assertEqual(cols["ts"], [0, 5])

    def test_columns_are_parallel_and_complete(self):
        _, _, cols = trace.window(packed(records([1, 2, 3])), 0, 3, 3, set())
        self.assertIsNotNone(cols)
        self.assertEqual({len(values) for values in cols.values()}, {3})
        self.assertEqual(set(cols), {"ts", "code", "cpu", "a", "b", "c"})

    def test_the_catalogue_carries_the_code_the_columns_use(self):
        """Otherwise a column-encoded record reaches the UI as a number
        with nothing to look it up in."""
        for entry in events.catalogue():
            with self.subTest(event=entry["id"]):
                self.assertEqual(entry["code"], events.BY_ID[entry["id"]].code)


class PackedWindowTest(unittest.TestCase):
    COUNT = 524_288

    @classmethod
    def setUpClass(cls):
        def one(code: int) -> bytes:
            return packed([trace.Record(ts=5, code=code, cpu=1, a=2, b=3, c=4)])

        cls.large = one(TRAP) * (cls.COUNT - 32) + one(BIND) * 32

    def scan(self, wanted: set[str]):
        original = trace._RECORD

        class Counted:
            calls = 0

            def iter_unpack(self, buffer):
                self.calls += 1
                return original.iter_unpack(buffer)

        counted = Counted()
        with mock.patch.object(trace, "_RECORD", counted):
            answer = trace.window(self.large, 0, 9, 128, wanted)
        self.assertEqual(counted.calls, 1)
        return answer

    def test_wide_and_narrow_answers_each_scan_the_large_window_once(self):
        count, hist, cols = self.scan(set())
        self.assertEqual(count, self.COUNT)
        self.assertIsNone(cols)
        self.assertEqual(sum(map(sum, hist.values())), self.COUNT)

        count, _hist, cols = self.scan({"vgic.bind"})
        self.assertEqual(count, 32)
        self.assertIsNotNone(cols)
        self.assertEqual(len(cols["ts"]), 32)

    def test_narrow_columns_keep_duplicates_gaps_and_unknown_codes(self):
        source = [
            trace.Record(ts=5, code=TRAP, cpu=0, a=1, b=2, c=3),
            trace.Record(ts=5, code=999, cpu=1, a=4, b=5, c=6),
            trace.Record(ts=6, code=trace.GAP_CODE, cpu=0, a=7, b=8, c=9),
        ]

        count, hist, cols = trace.window(packed(source), 0, 9, 3, set())

        self.assertEqual(count, 3)
        self.assertEqual(cols["ts"], [5, 5, 6])
        self.assertEqual(cols["code"], [TRAP, 999, trace.GAP_CODE])
        self.assertEqual(sum(map(sum, hist.values())), 2)


class ClientOwnershipTest(unittest.TestCase):
    class Socket:
        def __init__(self):
            self.request_id = ""
            self.sent = False

        def send(self, payload: str) -> None:
            self.request_id = json.loads(payload)["request_id"]

        def recv(self, timeout: float):
            del timeout
            if self.sent:
                raise TimeoutError
            self.sent = True
            columns = {"ts": [0], "code": [TRAP], "cpu": [0], "a": [0], "b": [0], "c": [0]}
            return json.dumps([
                {
                    "topic": "trace",
                    "kind": "snapshot",
                    "reply_to": "someone-else:1",
                    "data": {"window": {"from": 0, "n": 99}, "hist": {}},
                },
                {
                    "topic": "trace",
                    "kind": "snapshot",
                    "reply_to": self.request_id,
                    "data": {"window": {"from": 0, "n": 1}, "cols": columns},
                },
            ])

    def test_the_terminal_client_consumes_only_its_answer(self):
        records_, dense = client._window(self.Socket(), 0, 10, 10, client._RequestIds())

        self.assertEqual(len(records_), 1)
        self.assertEqual(dense, 0)


class WindowRequestTest(unittest.IsolatedAsyncioTestCase):
    """The uplink, against a bridge whose history is seeded directly."""

    def bridge(self, stamps=range(0, 100)):
        # No UI root and no phase: a window is answered from the history
        # alone, which is why its handler asks the session for nothing.
        from novakit.services.workbench.server import Bridge

        bridge = Bridge(ui_root=Path("/nonexistent"), trace_history=4096)
        bridge._history.append(records(stamps))
        bridge.store.drain()
        return bridge

    async def answer(self, bridge, **request) -> dict:
        """The reply, once the worker that built it is done.

        A window is answered off the loop, so the call that asks for one
        returns before the answer exists.
        """
        bridge._handle_uplink(json.dumps({
            "topic": "trace",
            "data": {"op": "window", **request},
            "request_id": "window:1",
        }))
        await bridge.settled()
        frames = bridge.store.drain()
        replies = [f for f in frames if f["topic"] == "trace" and f["kind"] == "snapshot"]
        self.assertEqual(len(replies), 1, f"expected one reply, got {frames}")
        return replies[0]["data"]

    async def rejection(self, bridge, **request) -> str:
        bridge._handle_uplink(json.dumps({
            "topic": "trace",
            "data": {"op": "window", **request},
            "request_id": "window:2",
        }))
        await bridge.settled()
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
        data = await self.answer(self.bridge(), **{"from": 10, "to": 19, "buckets": 100})
        self.assertEqual(data["window"]["n"], 10)
        self.assertEqual(data["cols"]["ts"], list(range(10)))
        self.assertNotIn("hist", data)

    async def test_a_wide_window_carries_density_and_no_marks(self):
        """More points than pixels is a density. The reader narrows the
        window to see marks, which is what dragging one is for."""
        data = await self.answer(self.bridge(), **{"from": 0, "to": 99, "buckets": 10})
        self.assertNotIn("cols", data)
        self.assertEqual(sum(data["hist"]["trap"]), 100, "a wide window still counts all of it")

    async def test_the_answer_states_the_horizon_it_was_taken_from(self):
        data = await self.answer(self.bridge(), **{"from": 0, "to": 99})
        self.assertEqual(data["span"]["n"], 100)
        self.assertFalse(data["span"]["full"])

    async def test_an_event_filter_narrows_what_comes_back(self):
        bridge = self.bridge()
        bridge._history.append(records([200], BIND))
        bridge.store.drain()
        data = await self.answer(bridge, **{"from": 0, "to": 500, "events": ["vgic.bind"]})
        self.assertEqual(data["window"]["n"], 1)
        self.assertEqual(data["cols"]["code"], [BIND])

    async def test_a_filter_applies_to_the_density_too(self):
        bridge = self.bridge()
        bridge._history.append(records([200], BIND))
        bridge.store.drain()
        data = await self.answer(bridge, **{"from": 0, "to": 500, "events": ["trap"], "buckets": 4})
        self.assertEqual(set(data["hist"]), {"trap"})
        self.assertEqual(sum(data["hist"]["trap"]), 100)

    async def test_an_absurd_resolution_is_refused_not_quietly_reduced(self):
        """The response arrays are as long as this number, and a caller
        that asked for a million columns has misunderstood something."""
        self.assertIn("buckets", await self.rejection(self.bridge(), buckets=10**6))

    async def test_a_backwards_window_is_refused(self):
        self.assertIn(
            "ends before", await self.rejection(self.bridge(), **{"from": 50, "to": 10})
        )

    async def test_an_unknown_op_is_refused_rather_than_guessed(self):
        bridge = self.bridge()
        bridge._handle_uplink(
            '{"topic":"trace","data":{"op":"everything"},"request_id":"window:3"}'
        )
        await bridge.settled()
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
        data = await self.answer(bridge, **{"from": 0, "to": 10})
        self.assertEqual(data["window"]["n"], 0)
        self.assertEqual(data["cols"]["ts"], [])

    async def test_one_connection_keeps_one_worker_and_only_the_latest_replacement(self):
        from novakit.services.workbench import server

        bridge = self.bridge()
        connection = object()
        bridge._connections.add(connection)
        active = 0
        peak = 0
        calls = []
        lock = threading.Lock()

        def build(_packed, _span, _freq, first, last, _buckets, _wanted):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                calls.append(first)
            time.sleep(0.02)
            with lock:
                active -= 1
            return {"window": {"from": first, "to": last, "n": 0}, "cols": {}}

        with mock.patch.object(server, "_window_payload", side_effect=build):
            for index in range(3):
                bridge._handle_uplink(
                    json.dumps({
                        "topic": "trace",
                        "data": {"op": "window", "from": index, "to": 10},
                        "request_id": f"burst:{index}",
                    }),
                    connection,
                )
            await bridge.settled()

        frames = bridge.store.drain()
        replies = [frame["reply_to"] for frame in frames if frame["topic"] == "trace"]
        rejected = [
            frame["reply_to"]
            for frame in frames
            if frame["data"].get("phase") == "uplink-rejected"
        ]
        self.assertEqual(calls, [0, 2])
        self.assertEqual(peak, 1)
        self.assertEqual(replies, ["burst:0", "burst:2"])
        self.assertEqual(rejected, ["burst:1"])

    async def test_two_connections_keep_their_out_of_order_answers_identified(self):
        from novakit.services.workbench import server

        bridge = self.bridge()
        slow, fast = object(), object()
        bridge._connections.update((slow, fast))

        def build(_packed, _span, _freq, first, last, _buckets, _wanted):
            time.sleep(0.03 if first == 1 else 0.005)
            return {"window": {"from": first, "to": last, "n": 0}, "cols": {}}

        with mock.patch.object(server, "_window_payload", side_effect=build):
            for connection, first, request_id in (
                (slow, 1, "slow:1"),
                (fast, 2, "fast:1"),
            ):
                bridge._handle_uplink(
                    json.dumps({
                        "topic": "trace",
                        "data": {"op": "window", "from": first, "to": 10},
                        "request_id": request_id,
                    }),
                    connection,
                )
            await bridge.settled()

        replies = [
            (frame["data"]["window"]["from"], frame["reply_to"])
            for frame in bridge.store.drain()
            if frame["topic"] == "trace"
        ]
        self.assertEqual(replies, [(2, "fast:1"), (1, "slow:1")])

    async def test_a_run_change_terminates_the_old_question_as_cancelled(self):
        from novakit.services.workbench import server

        bridge = self.bridge()

        def changes_run(*_args):
            bridge.session.run_id += 1
            return {"window": {}, "cols": {}}

        with mock.patch.object(server, "_window_payload", side_effect=changes_run):
            bridge._handle_uplink(json.dumps({
                "topic": "trace",
                "data": {"op": "window", "from": 0, "to": 10},
                "request_id": "old-run:1",
            }))
            await bridge.settled()

        terminal = [
            frame
            for frame in bridge.store.drain()
            if frame.get("reply_to") == "old-run:1"
        ]
        self.assertEqual(len(terminal), 1)
        self.assertEqual(terminal[0]["data"]["phase"], "query-cancelled")

    async def test_a_disconnected_question_publishes_no_orphaned_answer(self):
        from novakit.services.workbench import server

        bridge = self.bridge()
        connection = object()
        bridge._connections.add(connection)
        started = threading.Event()
        release = threading.Event()

        def build(*_args):
            started.set()
            release.wait(1)
            return {"window": {}, "cols": {}}

        with mock.patch.object(server, "_window_payload", side_effect=build):
            bridge._handle_uplink(json.dumps({
                "topic": "trace",
                "data": {"op": "window", "from": 0, "to": 10},
                "request_id": "gone:1",
            }), connection)
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            bridge._connections.discard(connection)
            bridge._disconnect_queries(connection)
            release.set()
            await bridge.settled()

        self.assertEqual(
            [frame for frame in bridge.store.drain() if frame.get("reply_to") == "gone:1"],
            [],
        )


if __name__ == "__main__":
    unittest.main()
