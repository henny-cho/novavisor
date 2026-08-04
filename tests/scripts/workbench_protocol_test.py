"""Envelope, batching, and static-file contracts of the bridge."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from novakit.services.workbench import protocol, static  # noqa: E402
from novakit.services.workbench.store import FrameWindow, StateStore  # noqa: E402


class FakeClock:
    def __init__(self):
        self.value = 0

    def monotonic_ns(self):
        return self.value


def envelopes(clock: FakeClock | None = None) -> protocol.Envelopes:
    return protocol.Envelopes(protocol.Clock((clock or FakeClock()).monotonic_ns))


class EnvelopeTest(unittest.TestCase):
    def test_shape_sequence_and_time_axis(self):
        clock = FakeClock()
        clock.value = 1_000
        factory = envelopes(clock)  # ts anchors at construction
        clock.value = 3_500
        first = factory.make(protocol.Topic.CONSOLE, protocol.Kind.EVENT, {"vm": 0})
        second = factory.make(
            protocol.Topic.EV,
            protocol.Kind.EVENT,
            {},
            src=protocol.Src.SERIAL,
        )

        self.assertEqual(
            first,
            {
                "v": protocol.PROTOCOL_VERSION,
                "seq": 1,
                "topic": "console",
                "kind": "event",
                "ts": 2_500,
                "src": "bridge",
                "data": {"vm": 0},
            },
        )
        self.assertEqual((second["seq"], second["src"]), (2, "serial"))

    def test_encode_is_always_an_array(self):
        frame = envelopes().make(protocol.Topic.LIFE, protocol.Kind.EVENT, {"phase": "booted"})
        decoded = json.loads(protocol.encode([frame]))
        self.assertIsInstance(decoded, list)
        self.assertEqual(decoded, [frame])
        self.assertEqual(json.loads(protocol.encode([])), [])


class UplinkTest(unittest.TestCase):
    def test_accepts_every_uplink_topic(self):
        for topic in protocol.UPLINK:
            with self.subTest(topic=topic.value):
                uplink = protocol.parse_uplink(json.dumps({"topic": topic.value, "data": {}}))
                self.assertEqual(uplink.topic, topic)

    def test_data_defaults_to_an_empty_object(self):
        self.assertEqual(protocol.parse_uplink('{"topic":"uart"}').data, {})

    def test_rejections(self):
        for text in (
            "not json",
            "[1,2]",
            '{"topic":"console","data":{}}',  # downlink topic
            '{"topic":"nope","data":{}}',
            '{"topic":"uart","data":[1]}',
        ):
            with self.subTest(text=text):
                with self.assertRaises(protocol.UplinkError):
                    protocol.parse_uplink(text)

    def test_control_bytes_decode(self):
        self.assertEqual(protocol.decode_bytes("ping\n"), b"ping\n")
        self.assertEqual(protocol.decode_bytes("\u0014"), b"\x14")


class FrameWindowTest(unittest.TestCase):
    def test_overflow_sheds_console_first(self):
        window = FrameWindow(max_frames=2)
        factory = envelopes()
        life = factory.make(protocol.Topic.LIFE, protocol.Kind.EVENT, {"phase": "booted"})
        console = factory.make(protocol.Topic.CONSOLE, protocol.Kind.EVENT, {"vm": 0})
        extra = factory.make(protocol.Topic.EV, protocol.Kind.EVENT, {})

        window.add(life)
        window.add(console)
        window.add(extra)

        self.assertEqual(window.drain(), [life, extra])
        self.assertEqual(window.dropped, 1)

    def test_overflow_without_console_sheds_oldest(self):
        window = FrameWindow(max_frames=1)
        factory = envelopes()
        first = factory.make(protocol.Topic.EV, protocol.Kind.EVENT, {"n": 1})
        second = factory.make(protocol.Topic.EV, protocol.Kind.EVENT, {"n": 2})

        window.add(first)
        window.add(second)

        self.assertEqual(window.drain(), [second])
        self.assertEqual(window.dropped, 1)

    def test_drain_clears(self):
        window = FrameWindow()
        window.add(envelopes().make(protocol.Topic.EV, protocol.Kind.EVENT, {}))
        self.assertEqual(len(window.drain()), 1)
        self.assertEqual(window.drain(), [])


class StateStoreTest(unittest.TestCase):
    def test_publish_reaches_window_and_backlog(self):
        store = StateStore(envelopes())
        frame = store.publish(protocol.Topic.CONSOLE, protocol.Kind.EVENT, {"vm": 0})

        self.assertEqual(store.drain(), [frame])
        self.assertIn(frame, store.connect_frames())

    def test_drain_reports_dropped_frames_once(self):
        store = StateStore(envelopes(), window=FrameWindow(max_frames=1))
        store.publish(protocol.Topic.CONSOLE, protocol.Kind.EVENT, {"vm": 0})
        store.publish(protocol.Topic.CONSOLE, protocol.Kind.EVENT, {"vm": 1})

        drained = store.drain()
        self.assertEqual(drained[-1]["topic"], "life")
        self.assertEqual(drained[-1]["data"], {"phase": "frames-dropped", "count": 1})
        self.assertEqual(store.drain(), [])

    def test_connect_replays_topology_first(self):
        store = StateStore(envelopes())
        store.set_topology({"cpus": 2})
        published = store.publish(protocol.Topic.LIFE, protocol.Kind.EVENT, {"phase": "booted"})
        store.drain()  # broadcast consumed before this client connected

        replay = store.connect_frames()

        self.assertEqual(replay[0]["topic"], "topo")
        self.assertEqual(replay[0]["data"], {"cpus": 2})
        self.assertEqual(replay[-1], published)
        # Replaying to one client must not re-broadcast to the others.
        self.assertEqual(store.drain(), [])


class StaticTest(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.root = Path(self._directory.name) / "ui"
        self.root.mkdir()
        (self.root / "index.html").write_text("<title>wb</title>")
        (self.root / "css").mkdir()
        (self.root / "css" / "tokens.css").write_text(":root {}")
        (self.root.parent / "secret.txt").write_text("keep out")

    def test_root_serves_index(self):
        reply = static.resolve(self.root, "/")
        self.assertEqual((reply.status, reply.content_type), (200, "text/html; charset=utf-8"))
        self.assertEqual(reply.body, b"<title>wb</title>")

    def test_mime_types_and_query_strings(self):
        reply = static.resolve(self.root, "/css/tokens.css?v=1")
        self.assertEqual((reply.status, reply.content_type), (200, "text/css; charset=utf-8"))

    def test_traversal_is_forbidden(self):
        for target in ("/../secret.txt", "/%2e%2e/secret.txt", "/css/../../secret.txt"):
            with self.subTest(target=target):
                self.assertEqual(static.resolve(self.root, target).status, 403)

    def test_missing_file_is_not_found(self):
        self.assertEqual(static.resolve(self.root, "/nope.js").status, 404)


if __name__ == "__main__":
    unittest.main()
