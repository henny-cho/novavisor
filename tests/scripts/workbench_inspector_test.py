"""Tests for workbench HaltController."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from novakit.services.workbench.dispatcher import Request  # noqa: E402
from novakit.services.workbench.inspector import HaltController  # noqa: E402
from novakit.services.workbench.protocol import Clock, Envelopes, Topic  # noqa: E402
from novakit.services.workbench.session import Session  # noqa: E402
from novakit.services.workbench.store import StateStore  # noqa: E402


class HaltControllerTest(unittest.TestCase):
    def setUp(self):
        self.store = StateStore(Envelopes(Clock()))
        self.session = Session(self.store)
        self.rejected = []
        self.tasks = []

        def reject_fn(reason, reply_to=None):
            self.rejected.append((reason, reply_to))

        async def ensure_poller():
            return None

        def spawn_fn(coro):
            task = asyncio.create_task(coro)
            self.tasks.append(task)
            return task

        self.controller = HaltController(
            self.store,
            self.session,
            reject_fn=reject_fn,
            ensure_poller_fn=ensure_poller,
            spawn_fn=spawn_fn,
        )

    def tearDown(self):
        for task in self.tasks:
            if not task.done():
                task.cancel()

    def test_with_delta_new_topic(self):
        data = {"pc": "0x1000", "HCR_EL2": "0x80000000"}
        res = HaltController.with_delta(data, {}, "sysreg")
        self.assertEqual(res, {"values": data})
        self.assertNotIn("changed", res)

    def test_with_delta_changed_topic(self):
        old_data = {"pc": "0x1000", "HCR_EL2": "0x80000000"}
        new_data = {"pc": "0x1004", "HCR_EL2": "0x80000000"}
        res = HaltController.with_delta(new_data, {"sysreg": old_data}, "sysreg")
        self.assertEqual(res["values"], new_data)
        self.assertIn("changed", res)

    def test_unknown_command_rejection(self):
        req = Request(connection=None, topic=Topic.HALT, data={"cmd": "invalid_cmd"}, request_id="req-bad")
        self.controller.take_halt(req)
        self.assertEqual(len(self.rejected), 1)
        self.assertIn("unknown cmd 'invalid_cmd'", self.rejected[0][0])
        self.assertEqual(self.rejected[0][1], "req-bad")

    def test_abort_command_sets_abort_flag(self):
        async def run_abort():
            await self.controller.halt_command("abort", {})

        asyncio.run(run_abort())
        self.assertTrue(self.controller.abort)


if __name__ == "__main__":
    unittest.main()
