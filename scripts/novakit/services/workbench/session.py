"""QEMU session lifecycle: one live child, one owner, one text path.

Every byte the firmware prints enters through `_on_readable` and leaves
as store frames; every phase transition is a published `life` event.
Blocking work (guest and hypervisor builds, child teardown) runs in the
default executor so the event loop keeps serving connections.
"""

from __future__ import annotations

import asyncio  # noqa: TID251 — the event loop lives here
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ...core import board
from ...image import observe
from .. import artifacts, cmake, expect, manifest, spawn, verify
from ..surfaces import Surfaces
from . import anchors, commands, derive, events, hardware, paths, steps
from .observations import observation_rates, timer_slot_labels
from .protocol import MAX_BUCKETS, Kind, Src, Topic
from .store import StateStore
from .taxonomy import vocabulary


class Phase(StrEnum):

    IDLE = "idle"
    BUILDING = "building"
    RUNNING = "running"
    VERIFYING = "verifying"
    EXITED = "exited"
    FAILED = "failed"
    # A run being read back from disk. Distinct from every phase above
    # because the machine is not merely stopped, it is absent: nothing
    # can be selected, stepped or resumed, and a command that pretended
    # otherwise would be a control that quietly does nothing.
    REPLAY = "replay"


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


def image_answers(elf: Path | None = None) -> observe.View | None:
    """What the build answered about an image, or nothing and why.

    Read fresh: milliseconds where the walk it replaces was seconds, and
    a cache would need a rule for when it had gone stale.

    Two absences, told apart. No image is not a fault — the topology
    goes out before the first build finishes. A built image whose view
    is missing or answers another question is refused with the reason
    said once, and the client still gets a board and a catalogue.
    """
    path = cmake.default_image() if elf is None else Path(elf)
    if not path.is_file():
        return None
    try:
        return observe.view_of(path)
    except (observe.Stale, OSError) as error:
        _say_once(str(error))
        return None


_SAID: set[str] = set()


def _say_once(message: str) -> None:
    if message not in _SAID:
        _SAID.add(message)
        print(f"[workbench] {message}", file=sys.stderr)


def image_capability(view: observe.View | None, tracing: bool = False) -> set[str]:
    """Which paths this run can show direct evidence for.

    `tracing` is false before a machine exists, which is honest rather
    than pessimistic: the rings are placed by EL2 well after the
    topology first goes out, and grading now for a layer that has not
    arrived is the overstatement the grades exist to prevent. The bridge
    republishes when it does arrive.
    """
    return events.observable(None if view is None else view.symbols, tracing)


def initial_topology() -> dict:
    """What a client sees before any target runs: the pickable world.

    The board map rides along, so the hardware picture is drawable
    before anything boots — it describes the machine, not the run.
    """
    view = image_answers()
    return {
        "demo": None,
        "guests": [],
        "board": hardware.board_map(direct=image_capability(view)),
        "catalog": _catalog(),
        "stops": events.catalogue(),
        "taxonomy": vocabulary() | derive.syndrome_vocabulary(view),
        "timer_slots": timer_slot_labels(),
        "observations": observation_rates(),
        "limits": {"buckets": MAX_BUCKETS},
        "ui_metadata": {
            "commands": commands.COMMAND_META,
            "edges": paths.EDGE_LABELS,
        },
    }



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
    # After the build, so what the image can show is read from the image
    # this run will use rather than from whatever preceded it.
    view = image_answers()
    topology = {
        "demo": name,
        "variant": target.variant,
        "description": demo_manifest.get("description", ""),
        # The placement the board map cannot state: where this run's
        # guests landed inside the window it describes.
        "guests": [
            {
                "name": guest.get("name"),
                "vcpus": guest.get("vcpus"),
                "pa": guest.get("load_addr"),
                "ipa": guest.get("ipa_base"),
                "size": guest.get("memory_size"),
                "uart": guest.get("uart", "none"),
            }
            for guest in demo_manifest.get("guests", [])
        ],
        "board": hardware.board_map(direct=image_capability(view)),
        "catalog": _catalog(),
        "stops": events.catalogue(),
        "taxonomy": vocabulary() | derive.syndrome_vocabulary(view),
        "timer_slots": timer_slot_labels(),
        "observations": observation_rates(),
        "limits": {"buckets": MAX_BUCKETS},
        "ui_metadata": {
            "commands": commands.COMMAND_META,
            "edges": paths.EDGE_LABELS,
        },
    }
    return Prepared(scenario, topology)


def _run_verify(scenario: expect.Scenario, stream, on_step, on_spawn, handlers=None) -> spawn.Run:
    """Blocking: one verification child, its console tee'd into `stream`."""
    return spawn.observe(
        scenario, stream=stream, on_step=on_step, on_spawn=on_spawn, handlers=handlers
    )


def _kill(child) -> None:
    try:
        child.terminate(force=True)
    except Exception:
        pass  # already gone


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
        # What the build resolved about this run's image.
        self.view: observe.View | None = None
        # H-layer machine state: the bridge sets it around QMP stop/cont
        # and every phase transition that replaces the machine clears it.
        self.paused = False
        # Bumped on every RUNNING transition: snapshot readers key their
        # resolved state on it, since a rebuild moves symbols.
        self.run_id = 0

    def regrade_paths(self, tracing: bool) -> None:
        """Republish the board with the paths this run can now witness.

        Capability is not settled when the topology first goes out: EL2
        places the trace rings well after it, and an edge that was grey
        because nothing could watch it becomes direct the moment
        something can. Publishing the upgrade is the alternative to
        promising it in advance.
        """
        topology = self._store.topology
        board = topology.get("board")
        if not board:
            return
        regraded = paths.edges(
            board["cpus"],
            [block["id"] for block in board["blocks"]],
            image_capability(image_answers(), tracing),
        )
        if regraded == board["edges"]:
            return
        self._store.set_topology(topology | {"board": board | {"edges": regraded}})

    def adopt_command_ring(self, facts: dict) -> None:
        """Publish what this run will accept being told.

        On the topology for the same reason the page tables are: a
        reader arriving late still has to know the machine can be driven
        and how long it takes to answer. Absent when the firmware placed
        no ring, which is the honest way to say a run cannot be driven.
        """
        self._store.set_topology(self._store.topology | {"command": facts})

    def adopt_guest_table(self, entries: list) -> None:
        """Replace what this run was asked for with what it built.

        The manifest is a request and the table EL2 filled is the
        answer; where they differ the answer is what is true of the
        machine on screen. So the placement becomes the machine's and
        only the name stays from the request — the one thing the
        firmware has none of.

        A difference is said as well as drawn: a guest that landed
        elsewhere is a generator that rounded or a configuration nobody
        reloaded, both worth knowing before reading anything placed
        against it.
        """
        topology = self._store.topology
        asked = topology.get("guests")
        if not isinstance(asked, list) or not entries:
            return
        built = {entry["vm"]: entry for entry in entries if isinstance(entry, dict) and "vm" in entry}
        merged = []
        differs = []
        for index, guest in enumerate(asked):
            entry = built.get(index)
            if entry is None:
                merged.append(guest)
                continue
            # kNone -> none: the enumerator's spelling is the
            # firmware's, the wire's has always been the manifest's.
            uart = str(entry.get("uart", "")).removeprefix("k").lower()
            says = {
                "name": guest.get("name"),
                "vcpus": entry.get("vcpus"),
                "pa": entry.get("pa"),
                "ipa": entry.get("ipa"),
                "size": entry.get("size"),
                "uart": uart,
            }
            if any(says[key] != guest.get(key) for key in says):
                differs.append(guest.get("name") or f"vm{index}")
            merged.append(guest | says)
        if merged == asked:
            return
        self._store.set_topology(topology | {"guests": merged})
        if differs:
            self._store.publish(
                Topic.LIFE,
                Kind.EVENT,
                {"phase": "guests-differ", "guests": differs},
            )

    def adopt_memory_map(self, captured: dict) -> None:
        """Publish this run's page tables, once they exist.

        Held on the topology rather than sent as a frame: the frame
        window sheds under load, where a late joiner still has to be able
        to walk. The same placement carries it into a recording and back
        out, since a replay adopts the world the run last published.
        """
        self._store.set_topology(self._store.topology | {"memory": captured})

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
            self.elf_path = Path(prepared.scenario.elf) if prepared.scenario.elf else None
            # Before the machine starts: the answers are a file, so the
            # observer stands by for the first instruction instead of
            # arriving seconds into the boot it was meant to watch.
            self.view = image_answers(self.elf_path) if self.elf_path else None
            try:
                self._live = self._deps.launch(tuple(command))
            except (Exception, SystemExit) as error:
                self._set_phase(Phase.FAILED, error=str(error))
                return
            self._assembler = anchors.LineAssembler()
            self._fd = self._live.fileno()
            loop.add_reader(self._fd, self._on_readable)
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
        total = len(prepared.scenario.steps)

        def on_step(step: expect.StepResult) -> None:
            loop.call_soon_threadsafe(self._publish_step, step, total)

        def on_spawn(child) -> None:
            # The worker owns this child and holds the session lock for
            # the whole scenario; publishing the handle is what lets
            # stop() reach in and end the run early.
            loop.call_soon_threadsafe(setattr, self, "_verify_child", child)

        # A verify run whose steps read the machine needs the machine to
        # be readable, and the bridge's own loops rest while it runs — so
        # the scenario's reader is the only one, and what it reads is
        # published here so the panels show the values that were judged.
        scenario, machine = prepared.scenario, None
        if expect.needs_observation(scenario.steps) and self.surfaces is not None:
            self.surfaces.reset()
            try:
                scenario, machine = verify.observable(
                    scenario, self.surfaces, scope="workbench",
                    on_reading=lambda topic, value: loop.call_soon_threadsafe(
                        self._publish_reading, topic, value
                    ),
                )
            except SystemExit as error:
                self._set_phase(Phase.FAILED, error=str(error))
                return

        try:
            run = await loop.run_in_executor(
                None, self._deps.run_verify, scenario, writer, on_step, on_spawn,
                None if machine is None else steps.handlers_for(machine),
            )
        except (Exception, SystemExit) as error:
            self._verify_child = None
            self._set_phase(Phase.FAILED, error=str(error))
            return
        finally:
            if machine is not None:
                machine.close()
        # Deliberately not a finally: on cancellation the worker thread
        # is still inside the scenario, and the handle is the only way
        # stop() can end it before the timeout.
        self._verify_child = None
        # The worker marshals text and step results with call_soon_threadsafe.
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
            "carried": len(result.results),
            "total": total,
        }
        if not result.ok:
            data["failure"] = result.failure.value if result.failure else "unknown"
            if result.step_kind:
                data["step"] = result.step
        self._store.publish(Topic.LIFE, Kind.EVENT, data, src=Src.SERIAL)
        self._set_phase(Phase.EXITED, code=0 if result.ok else 1)

    def _publish_reading(self, topic: str, value: object) -> None:
        """A value a step read, on the topic the panels already know.

        The same frame the poller would send, because it is the same
        reading — the scenario's reader is the only one during a verify
        run, so a panel drawn from anywhere else would be drawn from
        nothing.
        """
        self._store.publish(topic, Kind.SNAPSHOT, {"values": value}, src=Src.SNAP)

    def _publish_step(self, step: expect.StepResult, total: int) -> None:
        # The step's kind travels rather than the pattern it used to be:
        # a reader that assumes one kind cannot describe the others, and
        # the screen is the place that assumption becomes a blank label.
        self._store.publish(
            Topic.VERIFY,
            Kind.EVENT,
            {
                "index": step.index,
                "total": total,
                "kind": step.kind,
                "subject": step.subject,
                "elapsed": step.elapsed_seconds,
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
            target = self._store.topology.get("demo") or "current machine"
            self._store.publish(
                Topic.LIFE,
                Kind.EVENT,
                {"phase": "stop-failed", "target": target},
            )
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
