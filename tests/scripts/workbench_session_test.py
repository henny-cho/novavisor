"""Session lifecycle and socket-edge behaviour of the workbench bridge."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from novakit.services import expect, spawn  # noqa: E402
from novakit.services.workbench.protocol import Clock, Envelopes  # noqa: E402
from novakit.services.workbench.session import (  # noqa: E402
    Deps,
    Prepared,
    Session,
    Surfaces,
    Target,
)
from novakit.services.workbench.store import StateStore  # noqa: E402


class FakeLive:
    """A LiveSession stand-in over a socketpair.

    A real fd, so the event loop's add_reader path is genuinely
    exercised: the test writes firmware output into `peer` and reads
    console input back from it.
    """

    def __init__(self):
        self.sock, self.peer = socket.socketpair()
        self.peer.settimeout(2)
        self.exit_code = 0
        self.terminated = False

    def fileno(self) -> int:
        return self.sock.fileno()

    def read_available(self, size: int = 65536) -> bytes | None:
        try:
            data = self.sock.recv(size)
        except OSError:
            return None
        return data or None

    def write(self, data: bytes) -> None:
        self.sock.sendall(data)

    def poll_exit(self) -> int | None:
        return self.exit_code

    def terminate(self) -> bool:
        self.terminated = True
        self.sock.close()
        self.peer.close()
        return True


def scenario() -> expect.Scenario:
    return expect.Scenario(
        label="demo",
        phase=1,
        command=("qemu-system-aarch64", "-kernel", "novavisor.elf"),
        timeout_seconds=5,
        expectations=(),
    )


def deps_for(live: FakeLive) -> Deps:
    return Deps(
        prepare=lambda target: Prepared(scenario(), {"demo": target.demo}),
        launch=lambda _command: live,
    )


def store() -> StateStore:
    return StateStore(Envelopes(Clock()))


class SessionTest(unittest.IsolatedAsyncioTestCase):
    async def drain_until(self, state, predicate, timeout: float = 2.0) -> list[dict]:
        frames: list[dict] = []
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            frames.extend(state.drain())
            if predicate(frames):
                return frames
            await asyncio.sleep(0.01)
        self.fail(f"condition not met; frames={frames}")

    async def test_select_publishes_building_topo_running(self):
        live = FakeLive()
        self.addCleanup(live.terminate)
        state = store()
        session = Session(state, deps_for(live))

        await session.select(Target(demo="10_console_mux"))

        frames = state.drain()
        milestones = [
            (frame["topic"], frame["data"].get("phase"))
            for frame in frames
            if frame["topic"] in ("life", "topo")
        ]
        self.assertEqual(
            milestones,
            [("life", "building"), ("topo", None), ("life", "running")],
        )

    async def test_firmware_output_becomes_console_and_event_frames(self):
        live = FakeLive()
        self.addCleanup(live.terminate)
        state = store()
        session = Session(state, deps_for(live))
        await session.select(Target(demo="10_console_mux"))
        state.drain()

        live.peer.sendall(b"[vm0] echo: ping\n[smp] core 1 online\n")

        frames = await self.drain_until(
            state, lambda seen: sum(frame["topic"] == "console" for frame in seen) >= 2
        )
        console = [frame for frame in frames if frame["topic"] == "console"]
        self.assertEqual(console[0]["data"], {"vm": 0, "text": "echo: ping"})
        self.assertEqual(console[0]["src"], "serial")
        events = [frame for frame in frames if frame["topic"] == "ev"]
        self.assertEqual(events[0]["data"]["badge"], "SMP")
        self.assertEqual(events[0]["data"]["fields"], {"core": "1"})

    async def test_console_input_reaches_the_child_byte_exact(self):
        live = FakeLive()
        self.addCleanup(live.terminate)
        session = Session(store(), deps_for(live))
        await session.select(Target(demo="10_console_mux"))

        self.assertIsNone(session.send_bytes(b"ping\n"))
        self.assertIsNone(session.send_bytes(b"\x14"))  # the Ctrl-T contract

        received = b""
        while len(received) < 6:
            received += live.peer.recv(16)
        self.assertEqual(received, b"ping\n\x14")

    async def test_input_is_rejected_unless_running(self):
        session = Session(store(), deps_for(FakeLive()))
        self.assertEqual(session.send_bytes(b"x"), "session is idle")

    async def test_child_eof_publishes_the_exit_code(self):
        live = FakeLive()
        self.addCleanup(live.terminate)
        state = store()
        session = Session(state, deps_for(live))
        await session.select(Target(demo="10_console_mux"))
        state.drain()
        live.exit_code = 7

        live.peer.close()

        frames = await self.drain_until(
            state,
            lambda seen: any(frame["data"].get("phase") == "exited" for frame in seen),
        )
        exited = [frame for frame in frames if frame["data"].get("phase") == "exited"]
        self.assertEqual(exited[0]["data"], {"phase": "exited", "code": 7})

    async def test_prepare_failure_is_published_not_raised(self):
        def prepare(_target: Target) -> Prepared:
            raise SystemExit("no such demo")

        state = store()
        session = Session(state, Deps(prepare=prepare, launch=lambda _command: FakeLive()))

        await session.select(Target(demo="nope"))

        phases = [frame["data"].get("phase") for frame in state.drain()]
        self.assertIn("failed", phases)

    async def test_surfaces_attach_observation_args_to_the_launch(self):
        board_scenario = expect.Scenario(
            label="demo",
            phase=1,
            command=("qemu-system-aarch64", "-machine", "virt", "-m", "1024"),
            timeout_seconds=5,
            expectations=(),
        )
        live = FakeLive()
        self.addCleanup(live.terminate)
        launched: list[tuple[str, ...]] = []

        def launch(command):
            launched.append(command)
            return live

        with tempfile.TemporaryDirectory() as directory:
            surfaces = Surfaces(Path(directory))
            deps = Deps(
                prepare=lambda target: Prepared(board_scenario, {"demo": target.demo}),
                launch=launch,
            )
            session = Session(store(), deps, surfaces)

            await session.select(Target(demo="10_console_mux"))

            joined = " ".join(launched[0])
            self.assertIn("memory-backend-file,id=wbram,size=1024M", joined)
            self.assertIn(str(surfaces.shm_path), joined)
            self.assertIn(f"unix:{surfaces.qmp_path},server=on,wait=off", joined)
            self.assertIn("virt,memory-backend=wbram", joined)

    async def test_without_surfaces_the_command_is_untouched(self):
        live = FakeLive()
        self.addCleanup(live.terminate)
        launched: list[tuple[str, ...]] = []
        deps = Deps(
            prepare=lambda target: Prepared(scenario(), {"demo": target.demo}),
            launch=lambda command: (launched.append(command), live)[1],
        )

        await Session(store(), deps).select(Target(demo="10_console_mux"))

        self.assertEqual(launched[0], scenario().command)

    async def test_select_replaces_the_previous_child(self):
        first, second = FakeLive(), FakeLive()
        self.addCleanup(second.terminate)
        lives = iter((first, second))
        state = store()
        session = Session(
            state,
            Deps(
                prepare=lambda target: Prepared(scenario(), {"demo": target.demo}),
                launch=lambda _command: next(lives),
            ),
        )

        await session.select(Target(demo="10_console_mux"))
        await session.select(Target(demo="18_mixed"))

        self.assertTrue(first.terminated)
        self.assertFalse(second.terminated)


class VerifyStreamTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def verify_scenario() -> expect.Scenario:
        return expect.Scenario(
            label="demo",
            phase=1,
            command=("qemu-system-aarch64",),
            timeout_seconds=5,
            expectations=({"pattern": "a"}, {"pattern": "b"}, {"pattern": "c"}),
        )

    def deps_with(self, run_verify) -> Deps:
        return Deps(
            prepare=lambda target: Prepared(self.verify_scenario(), {"demo": target.demo}),
            launch=lambda _command: FakeLive(),
            run_verify=run_verify,
        )

    async def test_verify_streams_progress_console_and_outcome(self):
        # expect.observe_output numbers expectations from 1.
        matches = tuple(
            expect.PatternMatch(index, f"p{index}", float(index), 0.5, 4.0)
            for index in range(1, 4)
        )

        def run_verify(_scenario, stream, on_match) -> spawn.Run:
            stream.write("[vm0] echo: ping\n[smp] ")
            stream.write("core 1 online\n")
            for match in matches:
                on_match(match)
            return spawn.Run(
                expect.VerificationResult(matches=matches),
                spawn.OutputCapture(None),
            )

        state = store()
        session = Session(state, self.deps_with(run_verify))

        await session.select(Target(demo="10_console_mux", verify=True))

        frames = state.drain()
        phases = [frame["data"].get("phase") for frame in frames if frame["topic"] == "life"]
        self.assertEqual(
            phases,
            ["building", "verifying", "verify-pass", "exited"],
        )
        progress = [frame["data"] for frame in frames if frame["topic"] == "verify"]
        self.assertEqual(len(progress), 3)
        self.assertEqual(progress[0], {"index": 1, "total": 3, "pattern": "p1", "elapsed": 1.0})
        console = [frame["data"] for frame in frames if frame["topic"] == "console"]
        self.assertEqual(console[0], {"vm": 0, "text": "echo: ping"})
        events = [frame["data"] for frame in frames if frame["topic"] == "ev"]
        self.assertEqual(events[0]["badge"], "SMP")
        exited = [frame["data"] for frame in frames if frame["data"].get("phase") == "exited"]
        self.assertEqual(exited[0]["code"], 0)

    async def test_verify_failure_reports_kind_and_pattern(self):
        def run_verify(_scenario, _stream, _on_match) -> spawn.Run:
            return spawn.Run(
                expect.VerificationResult(
                    failure=expect.FailureKind.TIMEOUT,
                    pattern="echo: ping",
                ),
                spawn.OutputCapture(None),
            )

        state = store()
        session = Session(state, self.deps_with(run_verify))

        await session.select(Target(demo="10_console_mux", verify=True))

        frames = state.drain()
        outcome = [
            frame["data"] for frame in frames if frame["data"].get("phase") == "verify-fail"
        ]
        self.assertEqual(
            outcome[0],
            {
                "phase": "verify-fail",
                "matched": 0,
                "total": 3,
                "failure": "timeout",
                "pattern": "echo: ping",
            },
        )
        exited = [frame["data"] for frame in frames if frame["data"].get("phase") == "exited"]
        self.assertEqual(exited[0]["code"], 1)


class InitialTopologyTest(unittest.TestCase):
    def test_lists_the_pickable_world(self):
        from novakit.services.workbench.session import initial_topology

        topology = initial_topology()

        self.assertIsNone(topology["demo"])
        names = [entry["name"] for entry in topology["catalog"]]
        self.assertIn("10_console_mux", names)
        self.assertIn("badges", topology["taxonomy"])


@unittest.skipUnless(importlib.util.find_spec("websockets"), "websockets is not installed")
class ServerSmokeTest(unittest.IsolatedAsyncioTestCase):
    """Runs only where the pinned websockets package is installed."""

    @staticmethod
    def _get(port: int) -> bytes:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
            sock.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            chunks = []
            while chunk := sock.recv(4096):
                chunks.append(chunk)
        return b"".join(chunks)

    async def test_static_page_and_ws_frames_share_one_port(self):
        from novakit.services.workbench.server import Bridge
        from websockets.asyncio.client import connect

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "index.html").write_text("<title>wb</title>")
            bridge = Bridge(ui_root=root)
            await bridge.open("127.0.0.1", 0)
            try:
                page = await asyncio.to_thread(self._get, bridge.port)
                self.assertIn(b"200", page.split(b"\r\n", 1)[0])
                self.assertTrue(page.endswith(b"<title>wb</title>"))

                async with connect(f"ws://127.0.0.1:{bridge.port}/ws") as connection:
                    replay = json.loads(await asyncio.wait_for(connection.recv(), 2))
                    self.assertIsInstance(replay, list)
                    self.assertEqual(replay[0]["topic"], "topo")

                    await connection.send('{"topic":"cmd","data":{}}')
                    frames = json.loads(await asyncio.wait_for(connection.recv(), 2))
                    self.assertEqual(
                        frames[0]["data"],
                        {"phase": "unsupported", "topic": "cmd"},
                    )

                    await connection.send('{"topic":"qmp","data":{"cmd":"stop"}}')
                    frames = json.loads(await asyncio.wait_for(connection.recv(), 2))
                    self.assertEqual(
                        frames[0]["data"],
                        {"phase": "uplink-rejected", "reason": "qmp: session is idle"},
                    )
            finally:
                await bridge.close()


if __name__ == "__main__":
    unittest.main()
