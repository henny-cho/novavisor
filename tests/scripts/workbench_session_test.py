"""Session lifecycle and socket-edge behaviour of the workbench bridge."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import socket
import struct
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
    Phase,
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

    async def test_input_is_rejected_while_paused(self):
        live = FakeLive()
        self.addCleanup(live.terminate)
        session = Session(store(), deps_for(live))
        await session.select(Target(demo="10_console_mux"))

        session.paused = True
        self.assertEqual(session.send_bytes(b"x"), "machine is paused")

        session.paused = False
        self.assertIsNone(session.send_bytes(b"x"))

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

    async def test_console_write_failure_is_reported_not_raised(self):
        class BrokenPipe(FakeLive):
            def write(self, _data: bytes) -> None:
                raise OSError("pty gone")

        live = BrokenPipe()
        self.addCleanup(live.terminate)
        session = Session(store(), deps_for(live))
        await session.select(Target(demo="10_console_mux"))

        reason = session.send_bytes(b"ping\n")

        self.assertIsNotNone(reason)
        self.assertIn("console write failed", reason)

    async def test_stop_reports_a_child_that_survived(self):
        class Immortal(FakeLive):
            def terminate(self) -> bool:
                self.terminated = True
                return False

        live = Immortal()
        state = store()
        session = Session(state, deps_for(live))
        await session.select(Target(demo="10_console_mux"))
        state.drain()

        await session.stop()

        phases = [frame["data"].get("phase") for frame in state.drain()]
        self.assertEqual(phases, ["stop-failed", "idle"])

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

        def run_verify(_scenario, stream, on_match, _on_spawn) -> spawn.Run:
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
        def run_verify(_scenario, _stream, _on_match, _on_spawn) -> spawn.Run:
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


class VerifyInterruptTest(unittest.IsolatedAsyncioTestCase):
    async def test_stop_ends_an_active_verify_run(self):
        """stop() must reach the verify child even though the worker
        holds the session lock for the whole scenario."""
        import threading

        killed = threading.Event()

        class Child:
            def terminate(self, force: bool = False) -> bool:
                del force
                killed.set()
                return True

        def run_verify(_scenario, _stream, _on_match, on_spawn) -> spawn.Run:
            on_spawn(Child())
            if not killed.wait(timeout=5):
                raise AssertionError("verify child was never terminated")
            return spawn.Run(
                expect.VerificationResult(failure=expect.FailureKind.EOF),
                spawn.OutputCapture(None),
            )

        state = store()
        session = Session(
            state,
            Deps(
                prepare=lambda target: Prepared(
                    expect.Scenario(
                        label="demo",
                        phase=1,
                        command=("qemu-system-aarch64",),
                        timeout_seconds=5,
                        expectations=({"pattern": "a"},),
                    ),
                    {"demo": target.demo},
                ),
                launch=lambda _command: FakeLive(),
                run_verify=run_verify,
            ),
        )

        select = asyncio.create_task(session.select(Target(demo="10", verify=True)))
        deadline = asyncio.get_running_loop().time() + 2
        while session._verify_child is None:
            self.assertLess(asyncio.get_running_loop().time(), deadline, "spawn never surfaced")
            await asyncio.sleep(0.01)

        await session.stop()
        await select

        self.assertTrue(killed.is_set())
        phases = [frame["data"].get("phase") for frame in state.drain()]
        self.assertIn("verify-fail", phases)
        self.assertIn("exited", phases)


class SurfaceSweepTest(unittest.TestCase):
    def test_sweep_removes_only_dead_surfaces(self):
        import os

        from novakit.services.workbench.session import sweep_stale_surfaces

        with tempfile.TemporaryDirectory() as base_name:
            base = Path(base_name)
            old = (0, 0)  # epoch: comfortably past the age gate

            dead = base / "nova-wb-dead"
            dead.mkdir()
            (dead / "guest-ram").write_bytes(b"x")
            os.utime(dead, old)

            live = base / "nova-wb-live"
            live.mkdir()
            (live / "guest-ram").write_bytes(b"x")
            listener = socket.socket(socket.AF_UNIX)
            listener.bind(str(live / "qmp.sock"))
            listener.listen(1)
            os.utime(live, old)

            young = base / "nova-wb-young"
            young.mkdir()
            (young / "guest-ram").write_bytes(b"x")

            idle = base / "nova-wb-idle"
            idle.mkdir()
            os.utime(idle, old)

            try:
                sweep_stale_surfaces(base)
            finally:
                listener.close()

            self.assertFalse(dead.exists(), "a dead session's RAM file must be reclaimed")
            self.assertTrue(live.exists(), "an answering QMP socket marks a live bridge")
            self.assertTrue(young.exists(), "a starting bridge is never swept")
            self.assertTrue(idle.exists(), "an idle bridge holds no RAM file to reclaim")


class InitialTopologyTest(unittest.TestCase):
    def test_lists_the_pickable_world(self):
        from novakit.services.workbench.session import initial_topology

        topology = initial_topology()

        self.assertIsNone(topology["demo"])
        names = [entry["name"] for entry in topology["catalog"]]
        self.assertIn("10_console_mux", names)
        self.assertIn("badges", topology["taxonomy"])


def _no_image(_elf_path):
    """A scripted run has no ELF; the provider fake stands for both."""
    return None


class PollLoopTest(unittest.IsolatedAsyncioTestCase):
    """The S-layer loop against scripted providers: faults and restarts
    must end one run's polling, never the loop."""

    def bridge_with_run(self, directory: Path):
        from novakit.services.workbench.server import Bridge

        surfaces = Surfaces(directory)
        surfaces.shm_path.write_bytes(b"ram")
        bridge = Bridge(ui_root=directory, surfaces=surfaces)
        bridge.session.phase = Phase.RUNNING
        bridge.session.elf_path = directory / "novavisor.elf"
        bridge.session.run_id = 1
        # Resolve in-process. Where the work runs is environmental, and
        # a scripted image cannot cross a process boundary; this is the
        # same path a host that cannot start a pool takes.
        bridge._image_pool = lambda: None
        return bridge

    async def drain_until(self, bridge, predicate, timeout: float = 2.0) -> list[dict]:
        frames: list[dict] = []
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            frames.extend(bridge.store.drain())
            if predicate(frames):
                return frames
            await asyncio.sleep(0.01)
        self.fail(f"condition not met; frames={frames}")

    async def test_provider_fault_ends_the_run_not_the_loop(self):
        from unittest import mock

        from novakit.services.workbench import server as server_module

        class GoodProvider:
            def __init__(self):
                self.closed = False

            def read(self, _obs):
                return {"n": 1}

            def close(self):
                self.closed = True

        state = {"fail": True}

        def factory(_elf, _shm, _base, _view=None):
            if state["fail"]:
                raise RuntimeError("boom")
            return GoodProvider()

        with tempfile.TemporaryDirectory() as name:
            bridge = self.bridge_with_run(Path(name))
            # The image side is faked with the provider: a scripted run
            # has no ELF to resolve, and the split only matters to where
            # the resolving happens.
            with (
                mock.patch.object(server_module.snapshot, "resolve_image", _no_image),
                mock.patch.object(server_module.snapshot, "ElfRamProvider", factory),
            ):
                poll = asyncio.create_task(bridge._poll_loop())
                try:
                    await self.drain_until(
                        bridge,
                        lambda seen: any(
                            frame["data"].get("phase") == "snapshot-unavailable"
                            for frame in seen
                        ),
                    )
                    # The fault ended run 1's S layer; run 2 must poll again.
                    state["fail"] = False
                    bridge.session.run_id = 2
                    await self.drain_until(
                        bridge,
                        lambda seen: any(frame["topic"] == "sched.cpu" for frame in seen),
                    )
                finally:
                    poll.cancel()

    async def test_restart_during_build_discards_the_stale_provider(self):
        from unittest import mock

        from novakit.services.workbench import server as server_module

        class Provider:
            def __init__(self):
                self.closed = False

            def read(self, _obs):
                return {"n": 1}

            def close(self):
                self.closed = True

        instances: list[Provider] = []

        def factory(_elf, _shm, _base, _view=None):
            provider = Provider()
            instances.append(provider)
            if len(instances) == 1:
                # A restart lands while the DWARF walk is still running:
                # this provider maps the previous run's RAM file.
                bridge.session.run_id = 2
            return provider

        with tempfile.TemporaryDirectory() as name:
            bridge = self.bridge_with_run(Path(name))
            # The image side is faked with the provider: a scripted run
            # has no ELF to resolve, and the split only matters to where
            # the resolving happens.
            with (
                mock.patch.object(server_module.snapshot, "resolve_image", _no_image),
                mock.patch.object(server_module.snapshot, "ElfRamProvider", factory),
            ):
                poll = asyncio.create_task(bridge._poll_loop())
                try:
                    await self.drain_until(
                        bridge,
                        lambda seen: any(frame["topic"] == "sched.cpu" for frame in seen),
                    )
                finally:
                    poll.cancel()

        self.assertEqual(len(instances), 2)
        self.assertTrue(instances[0].closed, "the mid-build provider must be dropped")
        self.assertFalse(instances[1].closed)


class TraceAttachTest(unittest.TestCase):
    """Binding the T reader to a run.

    Two questions live here — does this image have a trace layer, and
    has the region been formatted yet — and the point of these tests is
    that neither is answered by counting failures of the other.
    """

    def setUp(self):
        from novakit.image import abi

        self.layout = abi.read_defines(
            abi.TRACE_RING,
            [
                "NOVA_TRACE_MAGIC",
                "NOVA_TRACE_VERSION",
                "NOVA_TRACE_SIZE",
                "NOVA_TRACE_HEADER_SIZE",
                "NOVA_TRACE_RECORDS_OFF",
                "NOVA_TRACE_REC_SIZE",
            ],
        )

    def region_bytes(self, *, formatted: bool, version: int | None = None, early: int = 0) -> bytes:
        buffer = bytearray(self.layout["NOVA_TRACE_SIZE"])
        if formatted:
            stride = self.layout["NOVA_TRACE_RECORDS_OFF"] + 16 * self.layout["NOVA_TRACE_REC_SIZE"]
            struct.pack_into(
                "<QIIIIIII", buffer, 0,
                self.layout["NOVA_TRACE_MAGIC"],
                self.layout["NOVA_TRACE_VERSION"] if version is None else version,
                self.layout["NOVA_TRACE_REC_SIZE"], stride, 1, 16, 62_500_000, early,
            )
        return bytes(buffer)

    def bridge_at(self, directory: Path):
        from novakit.services.workbench.server import Bridge

        surfaces = Surfaces(directory)
        bridge = Bridge(ui_root=directory, surfaces=surfaces)
        bridge.session.phase = Phase.RUNNING
        bridge.session.elf_path = directory / "novavisor.elf"
        bridge.session.run_id = 1
        # The region sits at the very start of the RAM aperture here, so
        # the fixture is the region and not half a gigabyte of run-up.
        bridge._board = {"NOVA_BOARD_PHYS_RAM_BASE": 0, "NOVA_BOARD_TRACE_PA": 0}
        return bridge

    def states(self, bridge) -> list[str]:
        return [
            frame["data"]["state"]
            for frame in bridge.store.drain()
            if frame["data"].get("phase") == "trace"
        ]

    def test_an_unformatted_region_is_waited_on_never_concluded_absent(self):
        """The old reader gave up after a fixed number of ticks, which
        on a slow machine told an image with tracing that it had none —
        and then called drain() on the None it had just decided on."""
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            bridge = self.bridge_at(directory)
            bridge.session.surfaces.shm_path.write_bytes(self.region_bytes(formatted=False))

            for _ in range(500):  # far past any budget the old code had
                self.assertFalse(bridge._attach_tracer())
            self.assertEqual(self.states(bridge), ["waiting"])  # said once

            # And a tick after all that must still be a working tick.
            bridge._pump_trace()

            # EL2 gets there eventually; nothing had to be reset for the
            # reader to notice.
            bridge.session.surfaces.shm_path.write_bytes(
                self.region_bytes(formatted=True, early=4)
            )
            self.assertTrue(bridge._attach_tracer())
            self.assertEqual(self.states(bridge), ["active"])
            self.assertEqual(bridge._tracer.geometry.early, 4)
            bridge._drop_tracer()

    def test_an_image_without_the_writer_says_so_and_keeps_looking(self):
        """The image answers whether to expect a ring, once the S layer
        has parsed it. A one-sided answer, so it changes what is said
        and not what is done."""

        class NoWriter:
            symbols = type("T", (), {"has": staticmethod(lambda _q: False)})()

        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            bridge = self.bridge_at(directory)
            bridge.session.surfaces.shm_path.write_bytes(self.region_bytes(formatted=False))

            # Before the index exists the answer is unknown, which is
            # not the same as no.
            self.assertFalse(bridge._attach_tracer())
            self.assertEqual(self.states(bridge), ["waiting"])

            bridge._provider = NoWriter()
            self.assertFalse(bridge._attach_tracer())
            self.assertEqual(self.states(bridge), ["none"])

            # A region turning up anyway wins: the report was a reading
            # of the image, not a verdict on the run.
            bridge.session.surfaces.shm_path.write_bytes(self.region_bytes(formatted=True))
            self.assertTrue(bridge._attach_tracer())
            self.assertEqual(self.states(bridge), ["active"])
            bridge._drop_tracer()

    def test_a_layout_disagreement_is_loud_and_not_retried_away(self):
        """A version skew is the one refusal asking again cannot fix,
        and decoding past it would produce plausible-looking events."""
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            bridge = self.bridge_at(directory)
            bridge.session.surfaces.shm_path.write_bytes(
                self.region_bytes(formatted=True, version=self.layout["NOVA_TRACE_VERSION"] + 1)
            )

            self.assertFalse(bridge._attach_tracer())
            self.assertEqual(self.states(bridge), ["mismatch"])


class ConnectionHandlerTest(unittest.IsolatedAsyncioTestCase):
    """The socket handler against a scripted connection: a client that
    dies is not a server fault, and one bad message is not a disconnect."""

    class FakeConnection:
        def __init__(self, messages=(), error=None):
            self.sent: list[str] = []
            self._messages = list(messages)
            self._error = error

        async def send(self, payload: str) -> None:
            self.sent.append(payload)

        async def __aiter__(self):
            for message in self._messages:
                yield message
            if self._error is not None:
                raise self._error

    def bridge(self):
        from novakit.services.workbench.server import Bridge

        return Bridge(ui_root=Path("/nonexistent"))

    async def test_hard_close_ends_the_connection_quietly(self):
        # websockets re-raises a non-clean close (a keepalive timeout, a
        # reset) out of the message iterator.
        bridge = self.bridge()
        connection = self.FakeConnection(error=ConnectionResetError("keepalive ping timeout"))

        await bridge._handler(connection)

        self.assertEqual(len(connection.sent), 1)  # the replay still went out
        self.assertNotIn(connection, bridge._connections)

    async def test_a_failing_message_costs_a_reply_not_the_socket(self):
        bridge = self.bridge()
        # A non-text frame reaches json.loads as a non-buffer: a TypeError
        # no uplink parser claims.
        connection = self.FakeConnection(messages=[5, '{"topic":"uart","data":{"bytes":"x"}}'])

        await bridge._handler(connection)

        phases = [
            frame["data"].get("reason", "")
            for frame in bridge.store.drain()
            if frame["data"].get("phase") == "uplink-rejected"
        ]
        self.assertTrue(any("uplink failed" in reason for reason in phases), phases)
        # Iteration continued: the second message was still delivered.
        self.assertTrue(any("session is idle" in reason for reason in phases), phases)

    async def test_abort_is_not_refused_by_the_advance_it_cancels(self):
        """An abort is only ever sent *while* an advance is running, so
        putting it behind the busy guard rejects it exactly when it is
        needed — and the advance then waits forever with no way out."""
        bridge = self.bridge()
        bridge._halting = True

        await bridge._halt_command("abort", {})

        self.assertTrue(bridge._abort)
        rejections = [
            frame["data"]
            for frame in bridge.store.drain()
            if frame["data"].get("phase") == "uplink-rejected"
        ]
        self.assertEqual(rejections, [])

    async def test_a_launch_that_never_happens_gives_up(self):
        """Arming watches for a run that may never start. An idle
        session means the launch failed or was replaced; the watcher
        must end rather than poll for the timeout."""
        bridge = self.bridge()

        await bridge._arm_at_launch(bridge.session.run_id, ["vgic.bind"])

        rejections = [
            frame["data"]
            for frame in bridge.store.drain()
            if frame["data"].get("phase") == "uplink-rejected"
        ]
        self.assertEqual(rejections, [])

    async def test_a_target_without_stops_arms_nothing(self):
        """The watcher stops the machine as it boots, so it must not be
        started for a plain run — that would pause every launch."""
        bridge = self.bridge()
        before = len(bridge._tasks)

        bridge._handle_uplink('{"topic":"target","data":{"demo":"01_hello"}}')

        # One task for the select; none for arming.
        self.assertEqual(len(bridge._tasks), before + 1)
        for task in tuple(bridge._tasks):
            task.cancel()

    async def test_every_other_command_still_waits_its_turn(self):
        bridge = self.bridge()
        bridge._halting = True

        await bridge._halt_command("run", {})

        reasons = [
            frame["data"].get("reason")
            for frame in bridge.store.drain()
            if frame["data"].get("phase") == "uplink-rejected"
        ]
        self.assertEqual(reasons, ["halt: inspection in progress"])


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
                    # Connect-time session state rides the fresh topo.
                    state = replay[0]["data"]
                    self.assertEqual(state["phase"], "idle")
                    self.assertFalse(state["paused"])
                    self.assertEqual(state["run_id"], 0)
                    self.assertTrue(state["session"])

                    # The connect topo was published, so the next flush
                    # re-broadcasts it; answers are found, not indexed.
                    await connection.send('{"topic":"cmd","data":{}}')
                    frames = json.loads(await asyncio.wait_for(connection.recv(), 2))
                    self.assertIn(
                        {"phase": "unsupported", "topic": "cmd"},
                        [frame["data"] for frame in frames],
                    )

                    await connection.send('{"topic":"halt","data":{"cmd":"stop"}}')
                    frames = json.loads(await asyncio.wait_for(connection.recv(), 2))
                    self.assertIn(
                        {"phase": "uplink-rejected", "reason": "halt: session is idle"},
                        [frame["data"] for frame in frames],
                    )
            finally:
                await bridge.close()


if __name__ == "__main__":
    unittest.main()
