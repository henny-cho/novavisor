"""Session lifecycle and socket-edge behaviour of the workbench bridge."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import socket
import tempfile
import unittest
from pathlib import Path

from novakit.services import expect, spawn
from novakit.services.surfaces import Surfaces
from novakit.services.workbench import snapshot, trace
from novakit.services.workbench.observations import Obs
from novakit.services.workbench.protocol import Clock, Envelopes
from novakit.services.workbench.session import (
    Deps,
    Phase,
    Prepared,
    Session,
    Target,
)
from novakit.services.workbench.store import StateStore
from novakit.services.workbench.trace_drain import (
    TRACE_DRAIN_FLOOR,
    TRACE_TURN_SECONDS,
)
from tests.support import bridge as support


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
        steps=(),
    )


def deps_for(live: FakeLive) -> Deps:
    return Deps(
        prepare=lambda target: Prepared(scenario(), {"demo": target.demo}),
        launch=lambda _command: live,
    )


def store() -> StateStore:
    return StateStore(Envelopes(Clock()))


class Draining(unittest.IsolatedAsyncioTestCase):
    """Waiting on a store until it has published what a test is after.

    Nothing below drives a clock, so every wait here is on real work
    landing: the point is to fail with the frames that did arrive rather
    than on a sleep somebody tuned.
    """

    async def drain_until(self, state, predicate, timeout: float = 2.0) -> list[dict]:
        frames: list[dict] = []
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            frames.extend(state.drain())
            if predicate(frames):
                return frames
            await asyncio.sleep(0.01)
        self.fail(f"condition not met; frames={frames}")


class SessionTest(Draining):
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
        live = FakeLive()
        self.addCleanup(live.terminate)
        session = Session(store(), deps_for(live))
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
            steps=(),
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

    async def test_the_run_takes_its_image_from_the_scenario(self):
        """Not from the command line it just built: that would be a
        second answer to a question the builder already answered."""
        live = FakeLive()
        self.addCleanup(live.terminate)
        carried = expect.Scenario(
            label="demo",
            phase=1,
            command=("qemu-system-aarch64", "-kernel", "/elsewhere/other.elf"),
            timeout_seconds=5,
            steps=(),
            elf=Path("/built/here/novavisor.elf"),
        )
        deps = Deps(
            prepare=lambda target: Prepared(carried, {"demo": target.demo}),
            launch=lambda _command: live,
        )
        session = Session(store(), deps)

        await session.select(Target(demo="10_console_mux"))

        self.assertEqual(session.elf_path, Path("/built/here/novavisor.elf"))

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
        # The override is a child that refuses to die, so the socketpair
        # it holds is released by the base implementation at cleanup.
        self.addCleanup(FakeLive.terminate, live)
        state = store()
        session = Session(state, deps_for(live))
        await session.select(Target(demo="10_console_mux"))
        state.drain()

        await session.stop()

        frames = state.drain()
        phases = [frame["data"].get("phase") for frame in frames]
        self.assertEqual(phases, ["stop-failed", "idle"])
        self.assertEqual(frames[0]["data"]["target"], "10_console_mux")

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
            steps=({"pattern": "a"}, {"pattern": "b"}, {"pattern": "c"}),
        )

    def deps_with(self, run_verify) -> Deps:
        return Deps(
            prepare=lambda target: Prepared(self.verify_scenario(), {"demo": target.demo}),
            launch=lambda _command: FakeLive(),
            run_verify=run_verify,
        )

    async def test_verify_streams_progress_console_and_outcome(self):
        # expect.observe_output numbers steps from 1.
        carried = tuple(
            expect.StepResult(index, "pattern", f"p{index}", float(index), 0.5, 4.0)
            for index in range(1, 4)
        )

        def run_verify(_scenario, stream, on_step, _on_spawn, _handlers=None) -> spawn.Run:
            stream.write("[vm0] echo: ping\n[smp] ")
            stream.write("core 1 online\n")
            for step in carried:
                on_step(step)
            return spawn.Run(
                expect.VerificationResult(results=carried),
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
        self.assertEqual(
            progress[0],
            {"index": 1, "total": 3, "kind": "pattern", "subject": "p1", "elapsed": 1.0},
        )
        console = [frame["data"] for frame in frames if frame["topic"] == "console"]
        self.assertEqual(console[0], {"vm": 0, "text": "echo: ping"})
        events = [frame["data"] for frame in frames if frame["topic"] == "ev"]
        self.assertEqual(events[0]["badge"], "SMP")
        exited = [frame["data"] for frame in frames if frame["data"].get("phase") == "exited"]
        self.assertEqual(exited[0]["code"], 0)

    async def test_verify_failure_reports_kind_and_step(self):
        def run_verify(_scenario, _stream, _on_step, _on_spawn, _handlers=None) -> spawn.Run:
            return spawn.Run(
                expect.VerificationResult(
                    failure=expect.FailureKind.TIMEOUT,
                    step_kind="pattern",
                    step_subject="echo: ping",
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
                "carried": 0,
                "total": 3,
                "failure": "timeout",
                "step": "/echo: ping/",
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

        def run_verify(_scenario, _stream, _on_step, on_spawn, _handlers=None) -> spawn.Run:
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
                        steps=({"pattern": "a"},),
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

        from novakit.services.surfaces import sweep_stale_surfaces

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


# initial_topology resolves observation symbols, so it needs the parser.
@unittest.skipUnless(importlib.util.find_spec("elftools"), "pyelftools is not installed")
class InitialTopologyTest(unittest.TestCase):
    def test_lists_the_pickable_world(self):
        from novakit.services.workbench.session import initial_topology

        topology = initial_topology()

        self.assertIsNone(topology["demo"])
        names = [entry["name"] for entry in topology["catalog"]]
        self.assertIn("10_console_mux", names)
        self.assertIn("badges", topology["taxonomy"])


class GuestTableTest(unittest.TestCase):
    """The manifest asks and the machine answers; the answer wins.

    A guest drawn at the address a configuration named, when the
    firmware put it somewhere else, is a picture of a machine that was
    intended. Everything placed against that address afterwards — a
    walk, a region, a fault — is placed against a fiction.
    """

    def session(self, guests):
        made = Session(store())
        made._store.set_topology({"demo": "x", "guests": guests})
        return made

    def frames(self, made):
        return [frame for frame in made._store.drain()]

    def test_the_placement_becomes_the_machines(self):
        made = self.session([{"name": "one", "vcpus": 2, "pa": 1, "ipa": 2, "size": 3, "uart": "none"}])
        made.adopt_guest_table(
            [{"vm": 0, "vmid": 1, "ipa": 0x4000, "pa": 0x8000, "size": 0x1000, "vcpus": 1, "uart": "kVuart"}]
        )
        (guest,) = made._store.topology["guests"]
        # The name is the request's; everything that places it is the
        # machine's, in the spelling the wire has always used.
        self.assertEqual(guest["name"], "one")
        self.assertEqual((guest["pa"], guest["ipa"], guest["size"]), (0x8000, 0x4000, 0x1000))
        self.assertEqual((guest["vcpus"], guest["uart"]), (1, "vuart"))

    def test_a_difference_is_said_as_well_as_drawn(self):
        made = self.session([{"name": "one", "vcpus": 1, "pa": 1, "ipa": 2, "size": 3, "uart": "none"}])
        made.adopt_guest_table(
            [{"vm": 0, "vmid": 1, "ipa": 2, "pa": 0x8000, "size": 3, "uart": "kNone", "vcpus": 1}]
        )
        said = [
            frame["data"]
            for frame in self.frames(made)
            if frame["data"].get("phase") == "guests-differ"
        ]
        self.assertEqual(said, [{"phase": "guests-differ", "guests": ["one"]}])

    def test_agreement_publishes_nothing(self):
        asked = {"name": "one", "vcpus": 1, "pa": 0x8000, "ipa": 0x4000, "size": 0x1000, "uart": "none"}
        made = self.session([asked])
        self.frames(made)  # the topology set above
        made.adopt_guest_table(
            [{"vm": 0, "vmid": 1, "ipa": 0x4000, "pa": 0x8000, "size": 0x1000, "vcpus": 1, "uart": "kNone"}]
        )
        self.assertEqual(self.frames(made), [])
        self.assertEqual(made._store.topology["guests"], [asked])

    def test_an_entry_the_machine_never_built_is_left_alone(self):
        """A VM that has not started yet has no table entry, and drawing
        it as absent would be a machine that lost a guest."""
        asked = {"name": "two", "vcpus": 1, "pa": 1, "ipa": 2, "size": 3, "uart": "none"}
        made = self.session([asked])
        made.adopt_guest_table([{"vm": 1, "vmid": 2, "ipa": 9, "pa": 9, "size": 9, "vcpus": 1, "uart": "kNone"}])
        self.assertEqual(made._store.topology["guests"], [asked])


class PollLoopTest(Draining):
    """The S-layer loop against scripted providers: faults and restarts
    must end one run's polling, never the loop."""

    def bridge_with_run(self, directory: Path):
        # A scripted run has no image, so nothing was resolved from one.
        # The provider fake stands for both, and the sentinel is only
        # there because "no view" is refused rather than polled.
        return support.running(directory, shm=b"ram", view=object())

    async def test_provider_fault_ends_the_run_not_the_loop(self):
        from unittest import mock

        from novakit.services.workbench import server as server_module

        class GoodProvider:
            def __init__(self):
                self.closed = False
                # No image behind it, so no page tables to publish, no
                # publisher behind it, so no firmware clock to quote, and
                # one topic to poll — the one the drains below wait for.
                self.regimes: dict = {}
                self.observations = (Obs("sched.cpu", "nova::vcpu::g_sched"),)

            def read(self, _obs, *, live=True, since=None):
                del live, since
                return snapshot.Reading({"n": 1})

            def close(self):
                self.closed = True

        state = {"fail": True}

        def factory(_elf, _shm, _base, _view=None):
            if state["fail"]:
                raise RuntimeError("boom")
            return GoodProvider()

        with tempfile.TemporaryDirectory() as name:
            bridge = self.bridge_with_run(Path(name))
            with mock.patch.object(server_module.snapshot, "open_provider", factory):
                poll = asyncio.create_task(bridge._poll_loop())
                try:
                    await self.drain_until(
                        bridge.store,
                        lambda seen: any(
                            frame["data"].get("phase") == "snapshot-unavailable"
                            for frame in seen
                        ),
                    )
                    # The fault ended run 1's S layer; run 2 must poll again.
                    state["fail"] = False
                    bridge.session.run_id = 2
                    await self.drain_until(
                        bridge.store,
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
                # No image behind it, so no page tables to publish, no
                # publisher behind it, so no firmware clock to quote, and
                # one topic to poll — the one the drains below wait for.
                self.regimes: dict = {}
                self.observations = (Obs("sched.cpu", "nova::vcpu::g_sched"),)

            def read(self, _obs, *, live=True, since=None):
                del live, since
                return snapshot.Reading({"n": 1})

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
            with mock.patch.object(server_module.snapshot, "open_provider", factory):
                poll = asyncio.create_task(bridge._poll_loop())
                try:
                    await self.drain_until(
                        bridge.store,
                        lambda seen: any(frame["topic"] == "sched.cpu" for frame in seen),
                    )
                finally:
                    poll.cancel()

        self.assertEqual(len(instances), 2)
        self.assertTrue(instances[0].closed, "the mid-build provider must be dropped")
        self.assertFalse(instances[1].closed)


# The region a board reserves is a board number, so a fixture states
# its own. Big enough for the one small ring below and nothing more.
REGION_SIZE = 0x10000


class TraceAttachTest(unittest.TestCase):
    """Binding the T reader to a run.

    Two questions live here — does this image have a trace layer, and
    has the region been formatted yet — and the point of these tests is
    that neither is answered by counting failures of the other.
    """

    def region_bytes(
        self,
        *,
        formatted: bool,
        version: int | None = None,
        early: int = 0,
        capacity: int = 16,
    ) -> bytes:
        """A RAM backend with, or without, a region placed at its start.

        Laid out by the reader's own packer: a header spelled here would
        be a second copy of the layout under test, right up until a field
        of it moves.
        """
        buffer = bytearray(REGION_SIZE)
        if formatted:
            trace.format_region(
                buffer, 0,
                rings=1, capacity=capacity, freq_hz=62_500_000, early=early,
                version=version,
            )
        return bytes(buffer)

    def bridge_at(self, directory: Path):
        # The region sits at the very start of the RAM aperture here, so
        # the fixture is the region and not half a gigabyte of run-up.
        return support.running(directory, board={
            "NOVA_BOARD_PHYS_RAM_BASE": 0,
            "NOVA_BOARD_TRACE_PA": 0,
            "NOVA_BOARD_TRACE_SIZE": REGION_SIZE,
        })

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
                self.assertFalse(bridge._trace_service.attach())
            self.assertEqual(self.states(bridge), ["waiting"])  # said once

            # And a tick after all that must still be a working tick.
            bridge._trace_service.pump()

            # EL2 gets there eventually; nothing had to be reset for the
            # reader to notice.
            bridge.session.surfaces.shm_path.write_bytes(
                self.region_bytes(formatted=True, early=4)
            )
            self.assertTrue(bridge._trace_service.attach())
            self.assertEqual(self.states(bridge), ["active"])
            self.assertEqual(bridge._trace_service.tracer.geometry.early, 4)
            bridge._trace_service.drop()

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
            self.assertFalse(bridge._trace_service.attach())
            self.assertEqual(self.states(bridge), ["waiting"])

            bridge._poller_service.provider = NoWriter()
            self.assertFalse(bridge._trace_service.attach())
            self.assertEqual(self.states(bridge), ["none"])

            # A region turning up anyway wins: the report was a reading
            # of the image, not a verdict on the run.
            bridge.session.surfaces.shm_path.write_bytes(self.region_bytes(formatted=True))
            self.assertTrue(bridge._trace_service.attach())
            self.assertEqual(self.states(bridge), ["active"])
            bridge._trace_service.drop()

    def test_a_layout_disagreement_is_loud_and_not_retried_away(self):
        """A version skew is the one refusal asking again cannot fix,
        and decoding past it would produce plausible-looking events."""
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            bridge = self.bridge_at(directory)
            bridge.session.surfaces.shm_path.write_bytes(
                self.region_bytes(formatted=True, version=trace.VERSION + 1)
            )

            self.assertFalse(bridge._trace_service.attach())
            self.assertEqual(self.states(bridge), ["mismatch"])

    def test_a_new_run_starts_a_new_epoch(self):
        """A restart begins the machine's clock again, so the last run's
        records are all *newer* than everything the new run emits. Kept,
        they win every window: the history lifts the whole buffer for each
        arriving record and evicts it by its own reinstatement, and the
        first stop of the new machine is compared against the old one's.
        """
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            bridge = self.bridge_at(directory)
            bridge.session.surfaces.shm_path.write_bytes(self.region_bytes(formatted=True))
            self.assertTrue(bridge._trace_service.attach())

            bridge._history.append(
                [trace.Record(ts=stamp, code=1, cpu=0, a=0, b=0, c=0)
                 for stamp in (1_000, 1_001, 1_002)]
            )
            bridge._halt_service.stopped_at = {"sysreg": {"pc": "0x1000"}}
            bridge._halt_service.inspector_run = bridge.session.run_id
            self.assertEqual(bridge._history.span().count, 3)

            bridge.session.run_id += 1
            self.assertTrue(bridge._trace_service.attach())
            bridge._halt_service.hold()

            self.assertEqual(bridge._history.span().count, 0, "kept the last run's records")
            self.assertEqual(bridge._halt_service.stopped_at, {}, "kept the last run's stop")
            # The new run's own records land, and are the whole of it.
            bridge._history.append([trace.Record(ts=5, code=1, cpu=0, a=0, b=0, c=0)])
            span = bridge._history.span()
            self.assertEqual((span.count, span.first, span.last), (1, 5, 5))
            bridge._trace_service.drop()

    def test_the_allowance_follows_the_time_a_turn_took(self):
        """A drain turn that overruns its budget stalls every other
        answer the bridge owes, so the allowance moves with what the
        last turn cost: halved when it overran, doubled only while the
        ring still had more, and never past the ring or under the floor.
        """
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            bridge = self.bridge_at(directory)
            bridge.session.surfaces.shm_path.write_bytes(
                self.region_bytes(formatted=True, capacity=128)
            )
            self.assertTrue(bridge._trace_service.attach())
            drain = bridge._trace_service
            self.assertEqual(drain.drain_limit, TRACE_DRAIN_FLOOR)

            # Cheap turn with the ring still behind: take more next time.
            drain.pace(TRACE_TURN_SECONDS / 4, capped=True)
            self.assertEqual(drain.drain_limit, TRACE_DRAIN_FLOOR * 2)
            # A cheap turn that emptied the ring asks for nothing more.
            drain.pace(TRACE_TURN_SECONDS / 4, capped=False)
            self.assertEqual(drain.drain_limit, TRACE_DRAIN_FLOOR * 2)
            # Overran: give the loop back to whoever else is waiting.
            drain.pace(TRACE_TURN_SECONDS * 2, capped=False)
            self.assertEqual(drain.drain_limit, TRACE_DRAIN_FLOOR)
            drain.pace(TRACE_TURN_SECONDS * 2, capped=False)
            self.assertEqual(drain.drain_limit, TRACE_DRAIN_FLOOR, "the floor holds")

            # Growth stops at the ring: a limit past it cannot be reached.
            for _ in range(4):
                drain.pace(TRACE_TURN_SECONDS / 4, capped=True)
            self.assertEqual(drain.drain_limit, 128)
            bridge._trace_service.drop()


class ConnectionHandlerTest(unittest.IsolatedAsyncioTestCase):
    """The socket handler against a scripted connection: a client that
    dies is not a server fault, and one bad message is not a disconnect."""

    class FakeConnection:
        def __init__(self, messages=(), error=None):
            self.sent: list[str] = []
            self._messages = list(messages)
            self._error = error
            self.closed = None

        async def send(self, payload: str) -> None:
            self.sent.append(payload)

        async def close(self, code: int, reason: str) -> None:
            self.closed = (code, reason)

        async def __aiter__(self):
            for message in self._messages:
                yield message
            if self._error is not None:
                raise self._error

    async def test_hard_close_ends_the_connection_quietly(self):
        # websockets re-raises a non-clean close (a keepalive timeout, a
        # reset) out of the message iterator.
        bridge = support.bridge()
        connection = self.FakeConnection(error=ConnectionResetError("keepalive ping timeout"))

        await bridge._handler(connection)

        self.assertEqual(len(connection.sent), 1)  # the replay still went out
        self.assertNotIn(connection, bridge._connections)

    async def test_a_message_without_an_identity_closes_only_its_socket(self):
        bridge = support.bridge()
        connection = self.FakeConnection(messages=[5])

        await bridge._handler(connection)

        self.assertEqual(connection.closed[0], 1008)
        self.assertEqual(
            [frame for frame in bridge.store.drain() if frame["topic"] == "life"], []
        )

    async def test_an_identified_bad_request_costs_a_reply_not_the_socket(self):
        bridge = support.bridge()
        connection = self.FakeConnection(messages=[
            '{"topic":"nonesuch","data":{},"request_id":"fake:1"}',
            '{"topic":"uart","data":{"bytes":"x"},"request_id":"fake:2"}',
        ])

        await bridge._handler(connection)

        reasons = [
            frame["data"].get("reason", "")
            for frame in bridge.store.drain()
            if frame["data"].get("phase") == "uplink-rejected"
        ]
        self.assertTrue(any("unknown uplink" in reason for reason in reasons), reasons)
        self.assertTrue(any("session is idle" in reason for reason in reasons), reasons)
        self.assertIsNone(connection.closed)

    async def test_abort_is_not_refused_by_the_advance_it_cancels(self):
        """An abort is only ever sent *while* an advance is running, so
        putting it behind the busy guard rejects it exactly when it is
        needed — and the advance then waits forever with no way out."""
        bridge = support.bridge()
        bridge._halt_service.halting = True

        await bridge._halt_service.halt_command("abort", {})

        self.assertTrue(bridge._halt_service.abort)
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
        bridge = support.bridge()

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
        bridge = support.bridge()
        before = len(bridge._tasks)

        bridge._handle_uplink(
            '{"topic":"target","data":{"demo":"01_hello"},"request_id":"target:1"}'
        )

        # One task for the select; none for arming.
        self.assertEqual(len(bridge._tasks), before + 1)
        for task in tuple(bridge._tasks):
            task.cancel()

    async def test_every_other_command_still_waits_its_turn(self):
        bridge = support.bridge()
        bridge._halt_service.halting = True

        await bridge._halt_service.halt_command("run", {})

        reasons = [
            frame["data"].get("reason")
            for frame in bridge.store.drain()
            if frame["data"].get("phase") == "uplink-rejected"
        ]
        self.assertEqual(reasons, ["halt: inspection in progress"])

    async def test_a_halt_command_that_is_not_one_is_refused_by_name(self):
        """The H layer's vocabulary is closed. An unknown command must be
        refused with the word in it and nothing spawned — a stray cmd
        that reached the inspector would take the machine for a stop
        nobody asked for."""
        bridge = support.bridge()
        bridge.session.phase = Phase.RUNNING
        bridge.session.surfaces = Surfaces(Path("/nonexistent"))

        bridge._handle_uplink(
            '{"topic":"halt","data":{"cmd":"invalid_cmd"},"request_id":"halt:1"}'
        )

        self.assertEqual(bridge._tasks, set())
        self.assertEqual(
            [
                (frame["reply_to"], frame["data"]["reason"])
                for frame in bridge.store.drain()
                if frame["data"].get("phase") == "uplink-rejected"
            ],
            [("halt:1", "halt: unknown cmd 'invalid_cmd'")],
        )

    async def test_the_first_stop_has_nothing_to_compare_against(self):
        """A stop publishes the whole machine and says what moved since
        the last one. The first stop of a run has no previous reading,
        and a mask taken against nothing would report every register as
        moved exactly when the reader has no way to know better."""

        class Stopped:
            """The gdb side of a stop, reduced to what a sweep reads."""

            def __init__(self, values):
                self.values = values

            def pause(self):
                return self.values

        def sysreg(bridge) -> dict:
            frames = [f for f in bridge.store.drain() if f["topic"] == "sysreg"]
            self.assertEqual(len(frames), 1, f"expected one sweep, got {frames}")
            return frames[0]["data"]

        bridge = support.bridge()
        await bridge._halt_service.sweep_to_panels(
            Stopped({"pc": "0x1000", "HCR_EL2": "0x80000000"})
        )
        self.assertEqual(
            sysreg(bridge), {"values": {"pc": "0x1000", "HCR_EL2": "0x80000000"}}
        )

        await bridge._halt_service.sweep_to_panels(
            Stopped({"pc": "0x1004", "HCR_EL2": "0x80000000"})
        )
        stop = sysreg(bridge)
        self.assertEqual(stop["values"]["pc"], "0x1004")
        self.assertEqual(stop["changed"], {"pc": True})


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

    async def until(self, connection, expected: dict, timeout: float = 2.0) -> None:
        """Read batches until one carries `expected`.

        Which batch an answer lands in is the flush loop's business: a
        flush falling between the send and the reply puts them in
        different ones. Reading exactly one batch asserts a schedule
        rather than a behaviour.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        seen: list[dict] = []
        while expected not in seen:
            left = deadline - loop.time()
            if left <= 0:
                self.fail(f"{expected} never arrived; saw {seen}")
            try:
                batch = json.loads(await asyncio.wait_for(connection.recv(), left))
            except TimeoutError:
                self.fail(f"{expected} never arrived; saw {seen}")
            seen += [frame["data"] for frame in batch]

    async def test_static_page_and_ws_frames_share_one_port(self):
        from websockets.asyncio.client import connect

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "index.html").write_text("<title>wb</title>")
            bridge = support.bridge(ui_root=root)
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

                    # The connect topo was published, so a later flush
                    # re-broadcasts it; answers are found, not indexed.
                    await connection.send(
                        '{"topic":"nonesuch","data":{},"request_id":"smoke:1"}'
                    )
                    await self.until(
                        connection,
                        {
                            "phase": "uplink-rejected",
                            "reason": "unknown uplink topic: 'nonesuch'",
                        },
                    )

                    await connection.send(
                        '{"topic":"halt","data":{"cmd":"stop"},"request_id":"smoke:2"}'
                    )
                    await self.until(
                        connection,
                        {"phase": "uplink-rejected", "reason": "halt: session is idle"},
                    )
            finally:
                await bridge.close()

class VerifyObservationTest(unittest.IsolatedAsyncioTestCase):
    """A verify run the bridge serves is made readable the same way one
    the demo runner serves is, and what its steps read reaches the
    panels — the bridge's own loops rest while it runs, so a panel drawn
    from anywhere else would be drawn from nothing."""

    @staticmethod
    def _run_verify(seen):
        def run_verify(scenario, _stream, _on_step, _on_spawn, handlers=None) -> spawn.Run:
            seen["command"] = scenario.command
            seen["handlers"] = None if handlers is None else sorted(handlers)
            return spawn.Run(expect.VerificationResult(), spawn.OutputCapture(None))

        return run_verify

    async def _served(self, scenario, demo):
        seen: dict = {}
        with tempfile.TemporaryDirectory() as directory:
            deps = Deps(
                prepare=lambda target: Prepared(scenario, {"demo": target.demo}),
                launch=lambda _command: None,
                run_verify=self._run_verify(seen),
            )
            session = Session(store(), deps, Surfaces(Path(directory)))
            await session.select(Target(demo=demo, verify=True))
        return seen

    async def test_a_scenario_that_observes_gets_surfaces_and_handlers(self):
        seen = await self._served(
            expect.Scenario(
                label="demo",
                phase=1,
                command=("qemu-system-aarch64", "-machine", "virt", "-m", "1024"),
                timeout_seconds=5,
                steps=({"observe": "smmu.stream"},),
                elf=Path("/built/novavisor.elf"),
            ),
            "15_dma_isolation",
        )

        self.assertIn("memory-backend=wbram", " ".join(seen["command"]))
        self.assertEqual(seen["handlers"], ["command", "event", "observe", "walk"])

    async def test_a_console_only_scenario_is_launched_as_it_always_was(self):
        seen = await self._served(
            expect.Scenario(
                label="demo",
                phase=1,
                command=("qemu-system-aarch64", "-machine", "virt", "-m", "1024"),
                timeout_seconds=5,
                steps=({"pattern": "ready"},),
            ),
            "01_hello",
        )

        self.assertNotIn("wbram", " ".join(seen["command"]))
        self.assertIsNone(seen["handlers"])

    async def test_a_reading_a_step_made_reaches_the_panels(self):
        state = store()
        session = Session(state, Deps(prepare=lambda _t: None, launch=lambda _c: None))

        session._publish_reading("smmu.stream", [{"stream": 16, "state": "abort"}])

        frames = [f for f in state.drain() if f["topic"] == "smmu.stream"]
        self.assertEqual(frames[0]["data"], {"values": [{"stream": 16, "state": "abort"}]})
