"""QEMU session lifecycle: one live child, one owner, one text path.

Every byte the firmware prints enters through `_on_readable` and leaves
as store frames; every phase transition is a published `life` event.
Blocking work (guest and hypervisor builds, child teardown) runs in the
default executor so the event loop keeps serving connections.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from .. import artifacts, expect, manifest, spawn
from . import anchors
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
    return {"demo": None, "guests": [], "catalog": _catalog(), "taxonomy": vocabulary()}


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
    }
    return Prepared(scenario, topology)


@dataclass(frozen=True)
class Deps:
    """Injection seam: tests swap the blocking edges, never the flow."""

    prepare: Callable[[Target], Prepared] = prepare
    launch: Callable[[tuple[str, ...]], spawn.LiveSession] = spawn.launch


class Session:
    def __init__(self, store: StateStore, deps: Deps | None = None):
        self._store = store
        self._deps = deps or Deps()
        self._lock = asyncio.Lock()
        self._assembler = anchors.LineAssembler()
        self._live: spawn.LiveSession | None = None
        self._fd: int | None = None
        self.phase = Phase.IDLE
        self.scenario: expect.Scenario | None = None

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
            try:
                self._live = self._deps.launch(prepared.scenario.command)
            except (Exception, SystemExit) as error:
                self._set_phase(Phase.FAILED, error=str(error))
                return
            self._assembler = anchors.LineAssembler()
            self._fd = self._live.fileno()
            loop.add_reader(self._fd, self._on_readable)
            self._set_phase(Phase.RUNNING, demo=target.demo)

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()

    async def _stop_locked(self) -> None:
        if self._live is None:
            return
        self._detach_reader()
        live, self._live = self._live, None
        # terminate() blocks on the child's demise; keep the loop alive.
        await asyncio.get_running_loop().run_in_executor(None, live.terminate)
        self._set_phase(Phase.IDLE)

    def send_bytes(self, data: bytes) -> str | None:
        """Forward console input; the rejection reason is the reply."""
        if self.phase is not Phase.RUNNING or self._live is None:
            return f"session is {self.phase.value}"
        self._live.write(data)
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
            self._set_phase(Phase.EXITED, code=live.poll_exit())
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
