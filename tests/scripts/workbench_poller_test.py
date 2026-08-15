"""Tests for observation poller and trace drain services."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from novakit.services.workbench import history  # noqa: E402
from novakit.services.workbench.poller import ObservationPoller  # noqa: E402
from novakit.services.workbench.protocol import Clock, Envelopes  # noqa: E402
from novakit.services.workbench.session import Session  # noqa: E402
from novakit.services.workbench.store import StateStore  # noqa: E402
from novakit.services.workbench.trace_drain import (  # noqa: E402
    TRACE_DRAIN_FLOOR,
    TRACE_TURN_SECONDS,
    TraceDrain,
)


class TraceDrainTest(unittest.TestCase):
    def setUp(self):
        self.store = StateStore(Envelopes(Clock()))
        self.session = Session(self.store)
        self.history = history.History(1024)
        self.drain = TraceDrain(
            self.store,
            lambda: self.history,
            self.session,
            recorder=None,
            board_numbers_fn=lambda: {"NOVA_BOARD_PHYS_RAM_BASE": 0, "NOVA_BOARD_TRACE_PA": 0, "NOVA_BOARD_TRACE_SIZE": 4096},
            image_has_tracing_fn=lambda: True,
        )

    def test_set_trace_state(self):
        self.drain.set_trace_state("active", reason="ok")
        self.assertEqual(self.drain.trace_state, "active")

        # Publishing duplicate state is a no-op
        self.drain.set_trace_state("active")
        self.assertEqual(self.drain.trace_state, "active")

    def test_pace_drain(self):
        fake_tracer = MagicMock()
        fake_tracer.geometry.capacity = 1024
        self.drain.tracer = fake_tracer
        self.drain.drain_limit = 256

        # Over budget -> halves limit
        self.drain.pace(TRACE_TURN_SECONDS + 0.005, capped=False)
        self.assertEqual(self.drain.drain_limit, 128)

        # Well under budget and capped -> doubles limit
        self.drain.pace(TRACE_TURN_SECONDS / 4, capped=True)
        self.assertEqual(self.drain.drain_limit, 256)

        # Floor constraint
        self.drain.drain_limit = TRACE_DRAIN_FLOOR
        self.drain.pace(TRACE_TURN_SECONDS + 0.005, capped=False)
        self.assertEqual(self.drain.drain_limit, TRACE_DRAIN_FLOOR)


class ObservationPollerTest(unittest.TestCase):
    def setUp(self):
        self.store = StateStore(Envelopes(Clock()))
        self.session = Session(self.store)
        self.poller = ObservationPoller(
            self.store,
            self.session,
            board_numbers_fn=lambda: {"NOVA_BOARD_PHYS_RAM_BASE": 0, "NOVA_BOARD_CMD_BASE": 0, "NOVA_BOARD_CMD_SIZE": 4096},
        )

    def test_drop_provider_cleans_resources(self):
        fake_provider = MagicMock()
        fake_writer = MagicMock()
        self.poller.provider = fake_provider
        self.poller.writer = fake_writer
        self.poller.provider_run = 1
        self.poller.writer_run = 1

        self.poller.drop_provider()

        fake_provider.close.assert_called_once()
        fake_writer.close.assert_called_once()
        self.assertIsNone(self.poller.provider)
        self.assertIsNone(self.poller.writer)
        self.assertIsNone(self.poller.provider_run)
        self.assertIsNone(self.poller.writer_run)

    def test_refresh_memory_map_with_no_capture(self):
        # Should safely do nothing if capture is None
        self.poller.capture = None
        self.poller.refresh_memory_map()


if __name__ == "__main__":
    unittest.main()
