"""QEMU session lifecycle: one live child, one owner, one text path.

Every byte the firmware prints enters through `_on_readable` and leaves
as store frames; every phase transition is a published `life` event.
Blocking work (guest and hypervisor builds, child teardown) runs in the
default executor so the event loop keeps serving connections.
"""

from __future__ import annotations

import asyncio
import shutil
import socket
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ...core import board
from .. import artifacts, expect, manifest, spawn
from . import anchors
from .observations import timer_slot_labels
from .protocol import Kind, Src, Topic
from .store import StateStore
from .taxonomy import vocabulary


class Phase(StrEnum):
    IDLE = "idle"
    BUILDING = "building"
    RUNNING = "running"
    VERIFYING = "verifying"
    EXITED = "exited"
    FAILED = "failed"


@dataclass(frozen=True)
class Target:
    demo: str
    variant: str | None = None
    verify: bool = False


@dataclass(frozen=True)
class Prepared:
    scenario: expect.Scenario
    topology: dict


def _catalog() -> list[dict]:
    return [
        {"id": manifest.demo_id(name), "name": name}
        for name, demo_manifest in manifest.iter_demos()
        if demo_manifest.get("enabled", False)
    ]


def initial_topology() -> dict:
    """What a client sees before any target runs: the pickable world."""
    return {
        "demo": None,
        "guests": [],
        "catalog": _catalog(),
        "taxonomy": vocabulary(),
        "timer_slots": timer_slot_labels(),
    }


def _kernel_of(command: list[str]) -> Path | None:
    try:
        return Path(command[command.index("-kernel") + 1])
    except (ValueError, IndexError):
        return None


def _select_variant(demo_manifest: dict, name: str | None) -> dict:
    variants = manifest.manifest_variants(demo_manifest)
    if name is None:
        return variants[0]
    for variant in variants:
        if variant.get("name") == name:
            return variant
    raise SystemExit(f"[workbench] unknown variant '{name}'")


def prepare(target: Target) -> Prepared:
    """Blocking: resolve the demo, build everything, describe the run."""
    name = manifest.resolve_demo(target.demo)
    _, demo_manifest = manifest.load_manifest(name)
    variant = _select_variant(demo_manifest, target.variant)
    scenario = artifacts.scenario_for(name, demo_manifest, variant)
    topology = {
        "demo": name,
        "variant": target.variant,
        "description": demo_manifest.get("description", ""),
        "guests": [
            {"name": guest.get("name"), "vcpus": guest.get("vcpus")}
            for guest in demo_manifest.get("guests", [])
        ],
        "catalog": _catalog(),
        "taxonomy": vocabulary(),
        "timer_slots": timer_slot_labels(),
    }
    return Prepared(scenario, topology)


def _run_verify(scenario: expect.Scenario, stream, on_match, on_spawn) -> spawn.Run:
    """Blocking: one verification child, its console tee'd into `stream`."""
    return spawn.observe(scenario, stream=stream, on_match=on_match, on_spawn=on_spawn)


def _kill(child) -> None:
    try:
        child.terminate(force=True)
    except Exception:
        pass  # already gone


@dataclass(frozen=True)
class Surfaces:
    """Filesystem endpoints of one bridge's observation surfaces.

    The owner (the server) creates and releases them; the session only
    attaches them to the QEMU command and resets them between runs so a
    restart never reads the previous run's RAM.
    """

    directory: Path

    @property
    def shm_path(self) -> Path:
        return self.directory / "guest-ram"

    @property
    def qmp_path(self) -> Path:
        return self.directory / "qmp.sock"

    @property
    def gdb_path(self) -> Path:
        return self.directory / "gdb.sock"

    def reset(self) -> None:
        self.shm_path.unlink(missing_ok=True)
        self.qmp_path.unlink(missing_ok=True)
        self.gdb_path.unlink(missing_ok=True)

    def release(self) -> None:
        self.reset()
        try:
            self.directory.rmdir()
        except OSError:
            pass


def sweep_stale_surfaces(base: Path, min_age_seconds: float = 60.0) -> None:
    """Remove observation directories whose bridge died without cleanup.

    A killed bridge (SIGKILL, a crashed terminal) leaves its RAM backend
    pinned in tmpfs — a gigabyte per Linux guest — until /dev/shm fills
    and every later QEMU launch fails. A directory is dead when its RAM
    file exists but nothing answers on its QMP socket; young directories
    are skipped so a bridge that is still starting is never swept.
    """
    now = time.time()
    for directory in base.glob("nova-wb-*"):
        try:
            if not (directory / "guest-ram").exists():
                continue
            if now - directory.stat().st_mtime < min_age_seconds:
                continue
            probe = directory / "qmp.sock"
            if probe.exists():
                with socket.socket(socket.AF_UNIX) as sock:
                    sock.settimeout(0.2)
                    try:
                        sock.connect(str(probe))
                        continue  # a live QEMU still answers here
                    except OSError:
                        pass
            shutil.rmtree(directory, ignore_errors=True)
        except OSError:
            continue


def make_surfaces() -> Surfaces:
    """tmpfs keeps the RAM file's dirtied pages off the disk; fall back
    to the default temp directory where /dev/shm is unavailable."""
    base = Path("/dev/shm")
    if base.is_dir():
        sweep_stale_surfaces(base)
    root = tempfile.mkdtemp(prefix="nova-wb-", dir=base if base.is_dir() else None)
    return Surfaces(Path(root))


@dataclass(frozen=True)
class Deps:
    """Injection seam: tests swap the blocking edges, never the flow."""

    prepare: Callable[[Target], Prepared] = prepare
    launch: Callable[[tuple[str, ...]], spawn.LiveSession] = spawn.launch
    run_verify: Callable[..., spawn.Run] = _run_verify


class _LoopWriter:
    """File-like tee for OutputCapture: text written by the verification
    worker thread is marshalled onto the loop that owns the store."""

    def __init__(self, loop: asyncio.AbstractEventLoop, callback: Callable[[str], None]):
        self._loop = loop
        self._callback = callback

    def write(self, text: str) -> None:
        self._loop.call_soon_threadsafe(self._callback, text)

    def flush(self) -> None:
        return None


class Session:
    def __init__(
        self,
        store: StateStore,
        deps: Deps | None = None,
        surfaces: Surfaces | None = None,
    ):
        self._store = store
        self._deps = deps or Deps()
        self._lock = asyncio.Lock()
        self._assembler = anchors.LineAssembler()
        self._live: spawn.LiveSession | None = None
        self._verify_child = None
        self._fd: int | None = None
        self.phase = Phase.IDLE
        self.scenario: expect.Scenario | None = None
        self.surfaces = surfaces
        self.elf_path: Path | None = None
        # H-layer machine state: the bridge sets it around QMP stop/cont
        # and every phase transition that replaces the machine clears it.
        self.paused = False
        # Bumped on every RUNNING transition: snapshot readers key their
        # resolved state on it, since a rebuild moves symbols.
        self.run_id = 0

    def _set_phase(self, phase: Phase, **data) -> None:
        self.phase = phase
        self._store.publish(Topic.LIFE, Kind.EVENT, {"phase": phase.value, **data})

    async def select(self, target: Target) -> None:
        async with self._lock:
            await self._stop_locked()
            self._set_phase(Phase.BUILDING, demo=target.demo)
            loop = asyncio.get_running_loop()
            try:
                prepared = await loop.run_in_executor(None, self._deps.prepare, target)
            except (Exception, SystemExit) as error:
                self._set_phase(Phase.FAILED, error=str(error))
                return
            self.scenario = prepared.scenario
            self._store.set_topology(prepared.topology)
            if target.verify:
                await self._verify_locked(target, prepared)
                return
            command = list(prepared.scenario.command)
            if self.surfaces is not None:
                self.surfaces.reset()
                command = board.attach_workbench(
                    command,
                    shm_path=self.surfaces.shm_path,
                    qmp_path=self.surfaces.qmp_path,
                    gdb_path=self.surfaces.gdb_path,
                )
            try:
                self._live = self._deps.launch(tuple(command))
            except (Exception, SystemExit) as error:
                self._set_phase(Phase.FAILED, error=str(error))
                return
            self._assembler = anchors.LineAssembler()
            self._fd = self._live.fileno()
            loop.add_reader(self._fd, self._on_readable)
            self.elf_path = _kernel_of(command)
            self.paused = False  # a fresh machine is running by definition
            self.run_id += 1
            self._set_phase(Phase.RUNNING, demo=target.demo)

    async def _verify_locked(self, target: Target, prepared: Prepared) -> None:
        """Run the demo's verification scenario in its own child.

        `observe` always terminates its child, so a verify run never
        shares the interactive pty; its console text still flows through
        the same assembler and anchor pipeline.
        """
        self._set_phase(Phase.VERIFYING, demo=target.demo)
        loop = asyncio.get_running_loop()
        self._assembler = anchors.LineAssembler()
        writer = _LoopWriter(loop, self._ingest_text)
        total = len(prepared.scenario.expectations)

        def on_match(match: expect.PatternMatch) -> None:
            loop.call_soon_threadsafe(self._publish_match, match, total)

        def on_spawn(child) -> None:
            # The worker owns this child and holds the session lock for
            # the whole scenario; publishing the handle is what lets
            # stop() reach in and end the run early.
            loop.call_soon_threadsafe(setattr, self, "_verify_child", child)

        try:
            run = await loop.run_in_executor(
                None, self._deps.run_verify, prepared.scenario, writer, on_match, on_spawn
            )
        except (Exception, SystemExit) as error:
            self._verify_child = None
            self._set_phase(Phase.FAILED, error=str(error))
            return
        # Deliberately not a finally: on cancellation the worker thread
        # is still inside the scenario, and the handle is the only way
        # stop() can end it before the timeout.
        self._verify_child = None
        # The worker marshals text and matches with call_soon_threadsafe.
        # A fast-finishing child can complete the executor future before
        # it is even awaited, and awaiting a done future never yields —
        # skipping the queued callbacks. One yield lets them land, so
        # progress frames always precede the outcome frame.
        await asyncio.sleep(0)
        for raw in self._assembler.flush():
            self._ingest(raw)
        result = run.result
        data = {
            "phase": "verify-pass" if result.ok else "verify-fail",
            "matched": len(result.matches),
            "total": total,
        }
        if not result.ok:
            data["failure"] = result.failure.value if result.failure else "unknown"
            if result.pattern:
                data["pattern"] = result.pattern
        self._store.publish(Topic.LIFE, Kind.EVENT, data, src=Src.SERIAL)
        self._set_phase(Phase.EXITED, code=0 if result.ok else 1)

    def _publish_match(self, match: expect.PatternMatch, total: int) -> None:
        self._store.publish(
            Topic.VERIFY,
            Kind.EVENT,
            {
                "index": match.index,
                "total": total,
                "pattern": match.pattern,
                "elapsed": match.elapsed_seconds,
            },
            src=Src.SERIAL,
        )

    def _ingest_text(self, text: str) -> None:
        for raw in self._assembler.feed(text.encode("utf-8")):
            self._ingest(raw)

    async def stop(self) -> None:
        child, self._verify_child = self._verify_child, None
        if child is not None:
            # A verify run holds the lock for its whole scenario; ending
            # the child is what makes the worker — and the lock — come
            # back now instead of at the scenario timeout.
            await asyncio.get_running_loop().run_in_executor(None, _kill, child)
        async with self._lock:
            await self._stop_locked()

    async def _stop_locked(self) -> None:
        if self._live is None:
            return
        self._detach_reader()
        live, self._live = self._live, None
        # terminate() blocks on the child's demise; keep the loop alive.
        dead = await asyncio.get_running_loop().run_in_executor(None, live.terminate)
        if not dead:
            # A child that survived SIGKILL keeps its RAM backend pinned
            # in tmpfs; say so instead of pretending the slate is clean.
            self._store.publish(Topic.LIFE, Kind.EVENT, {"phase": "stop-failed"})
        self.paused = False
        self._set_phase(Phase.IDLE)

    def send_bytes(self, data: bytes) -> str | None:
        """Forward console input; the rejection reason is the reply."""
        if self.phase is not Phase.RUNNING or self._live is None:
            return f"session is {self.phase.value}"
        if self.paused:
            # The pty would buffer the bytes and replay them into the
            # guest on resume — accepted input that acts later is worse
            # than a visible rejection.
            return "machine is paused"
        try:
            self._live.write(data)
        except OSError as error:
            # The child died between the phase check and the write; a
            # dead pty must cost this request, not the connection.
            return f"console write failed: {error}"
        return None

    def _detach_reader(self) -> None:
        # Always before terminate(): a reader on a dead fd spins the loop.
        if self._fd is not None:
            asyncio.get_running_loop().remove_reader(self._fd)
            self._fd = None

    def _on_readable(self) -> None:
        if self._live is None:
            return
        chunk = self._live.read_available()
        if chunk is None:
            self._detach_reader()
            live, self._live = self._live, None
            for raw in self._assembler.flush():
                self._ingest(raw)
            code = live.poll_exit()
            if code is None:
                # A pty error with the child still alive: reap it off
                # the loop instead of leaving it to the GC finalizer,
                # which sleeps inside this reader callback.
                future = asyncio.get_running_loop().run_in_executor(None, live.terminate)
                future.add_done_callback(lambda done: done.exception())
            self._set_phase(Phase.EXITED, code=code)
            return
        for raw in self._assembler.feed(chunk):
            self._ingest(raw)

    def _ingest(self, raw: str) -> None:
        line = anchors.split(raw)
        self._store.publish(
            Topic.CONSOLE,
            Kind.EVENT,
            {"vm": line.vm, "text": line.text},
            src=Src.SERIAL,
        )
        for anchor in anchors.classify(line):
            self._store.publish(
                Topic.EV,
                Kind.EVENT,
                {
                    "badge": anchor.badge.value,
                    "severity": anchor.severity.value,
                    "message": anchor.message,
                    "fields": anchor.fields,
                },
                src=Src.SERIAL,
            )
        outcome = anchors.lifecycle(line)
        if outcome is not None:
            phase, data = outcome
            self._store.publish(Topic.LIFE, Kind.EVENT, {"phase": phase, **data}, src=Src.SERIAL)
