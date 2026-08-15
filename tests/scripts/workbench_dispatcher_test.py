"""Tests for uplink dispatching and query concurrency management."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from novakit.services.workbench.dispatcher import (  # noqa: E402
    Dispatcher,
    Handler,
    Needs,
    Request,
)
from novakit.services.workbench.protocol import Topic  # noqa: E402
from novakit.services.workbench.session import Phase  # noqa: E402


class DispatcherTest(unittest.TestCase):
    def setUp(self):
        self.rejections: list[tuple[str, str | None]] = []
        self.cancellations: list[tuple[Request, str]] = []
        self.spawned: list[object] = []

    def tearDown(self):
        for coro in self.spawned:
            if asyncio.iscoroutine(coro):
                coro.close()

    def _reject(self, reason: str, reply_to: str | None = None) -> None:
        self.rejections.append((reason, reply_to))

    def _cancel(self, request: Request, reason: str) -> None:
        self.cancellations.append((request, reason))

    def _spawn(self, coro) -> None:
        self.spawned.append(coro)

    def test_unmet_needs(self):
        d = Dispatcher((), self._reject, self._cancel, self._spawn)

        # NOTHING is never unmet
        self.assertIsNone(d.unmet(Needs.NOTHING, Phase.IDLE, None, is_replay=False))
        self.assertIsNone(d.unmet(Needs.NOTHING, Phase.RUNNING, object(), is_replay=True))

        # MACHINE is unmet when replay is active
        self.assertIsNone(d.unmet(Needs.MACHINE, Phase.IDLE, None, is_replay=False))
        self.assertIsNotNone(d.unmet(Needs.MACHINE, Phase.IDLE, None, is_replay=True))

        # RUNNING is unmet when phase != RUNNING or surfaces is None
        self.assertIsNotNone(d.unmet(Needs.RUNNING, Phase.IDLE, None, is_replay=False))
        self.assertIsNotNone(d.unmet(Needs.RUNNING, Phase.RUNNING, None, is_replay=False))
        self.assertIsNone(d.unmet(Needs.RUNNING, Phase.RUNNING, object(), is_replay=False))

        # REPLAY is unmet when not replay
        self.assertIsNotNone(d.unmet(Needs.REPLAY, Phase.RUNNING, object(), is_replay=False))
        self.assertIsNone(d.unmet(Needs.REPLAY, Phase.IDLE, None, is_replay=True))

    def test_handle_uplink_rejection_for_unmet_precondition(self):
        handled = []

        def _call_cmd(_bridge, req):
            handled.append(req)

        handler = Handler(Topic.CMD, _call_cmd, needs=Needs.MACHINE)
        d = Dispatcher((handler,), self._reject, self._cancel, self._spawn)

        # In replay mode, CMD is rejected
        d.handle_uplink(
            bridge=None,  # type: ignore
            message='{"topic":"cmd","request_id":"req-1","data":{"word":1}}',
            connection=None,
            phase=Phase.IDLE,
            surfaces=None,
            is_replay=True,
            closing=False,
            connections=set(),
        )
        self.assertEqual(len(handled), 0)
        self.assertEqual(len(self.rejections), 1)
        self.assertEqual(self.rejections[0][1], "req-1")
        self.assertIn("replay", self.rejections[0][0])

    def test_query_concurrency_and_replacement(self):
        handled = []

        async def _call_trace(_bridge, req):
            handled.append(req)

        handler = Handler(Topic.TRACE, _call_trace, query=True)
        d = Dispatcher((handler,), self._reject, self._cancel, self._spawn)

        conn = object()
        connections = {conn}
        req1 = Request(conn, Topic.TRACE, {}, "q-1")
        req2 = Request(conn, Topic.TRACE, {}, "q-2")
        req3 = Request(conn, Topic.TRACE, {}, "q-3")

        # First request creates active slot
        d.schedule_query(None, handler, req1, False, connections)  # type: ignore
        key = (conn, Topic.TRACE)
        self.assertIn(key, d._queries)
        self.assertEqual(d._queries[key].active, req1)
        self.assertIsNone(d._queries[key].replacement)

        # Second request sets replacement
        d.schedule_query(None, handler, req2, False, connections)  # type: ignore
        self.assertEqual(d._queries[key].replacement, req2)
        self.assertEqual(len(self.rejections), 0)

        # Third request supersedes second request
        d.schedule_query(None, handler, req3, False, connections)  # type: ignore
        self.assertEqual(d._queries[key].replacement, req3)
        self.assertEqual(len(self.rejections), 1)
        self.assertEqual(self.rejections[0][1], "q-2")

        # Disconnecting cleans up query slot
        d.disconnect_queries(conn)
        self.assertNotIn(key, d._queries)


if __name__ == "__main__":
    unittest.main()
