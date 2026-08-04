"""The socket edge: one port serves the UI and fans out frames.

Only this module touches websockets — lazily, mirroring the optional
YAML import — and owns the flush cadence. Everything it forwards is a
store frame produced elsewhere, so the wire content stays testable
without a socket.
"""

from __future__ import annotations

import asyncio
import signal
import sys
import uuid
from pathlib import Path

from ...core import config
from . import halt, snapshot, static
from .protocol import (
    SUPPORTED_UPLINK,
    Clock,
    Envelopes,
    Kind,
    Src,
    Topic,
    UplinkError,
    decode_bytes,
    encode,
    parse_uplink,
)
from .session import (
    Deps,
    Phase,
    Session,
    Surfaces,
    Target,
    initial_topology,
    make_surfaces,
)
from .store import StateStore

FLUSH_INTERVAL_SECONDS = 0.05
POLL_INTERVAL_SECONDS = 0.05
WS_PATH = "/ws"


def _require_websockets():
    try:
        from websockets.asyncio import server as websocket_server
        from websockets.datastructures import Headers
        from websockets.http11 import Response
    except ImportError as error:
        raise SystemExit(
            "nova workbench: the pinned websockets package is missing; "
            "run scripts/python-env"
        ) from error
    return websocket_server, Headers, Response


class Bridge:
    """A running bridge: the store, the session, and the serving socket."""

    def __init__(
        self,
        *,
        ui_root: Path,
        deps: Deps | None = None,
        surfaces: Surfaces | None = None,
    ):
        self.store = StateStore(Envelopes(Clock()))
        self.session = Session(self.store, deps, surfaces)
        self._ui_root = ui_root
        self._connections: set = set()
        self._tasks: set[asyncio.Task] = set()
        self._server = None
        self._flusher: asyncio.Task | None = None
        self._halting = False
        # Stamped into every connect topo: a changed token is the one
        # reliable restart signal, whatever the seq counter says.
        self._token = uuid.uuid4().hex[:8]

    async def open(self, host: str, port: int) -> None:
        websocket_server, headers_type, response_type = _require_websockets()

        def process_request(_connection, request):
            if request.path == WS_PATH:
                return None  # proceed with the WebSocket handshake
            reply = static.resolve(self._ui_root, request.path)
            headers = headers_type()
            headers["Content-Type"] = reply.content_type
            headers["Content-Length"] = str(len(reply.body))
            headers["Connection"] = "close"
            return response_type(reply.status, reply.reason, headers, reply.body)

        self._server = await websocket_server.serve(
            self._handler, host, port, process_request=process_request
        )
        self._flusher = asyncio.create_task(self._flush_loop())
        if self.session.surfaces is not None:
            self.spawn(self._poll_loop())

    @property
    def port(self) -> int:
        return self._server.sockets[0].getsockname()[1]

    def spawn(self, coroutine) -> None:
        """Run session work without dropping the task reference."""
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._reap)

    def _reap(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            # A worker that dies silently reads as a feature that just
            # stopped; put the loss on the wire where the UI shows it.
            self.store.publish(
                Topic.LIFE, Kind.EVENT, {"phase": "task-failed", "error": str(error)}
            )

    async def close(self) -> None:
        if self._flusher is not None:
            self._flusher.cancel()
        for task in tuple(self._tasks):
            task.cancel()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        await self.session.stop()

    def _live_state(self) -> dict:
        """Session truth a late joiner cannot recover from the backlog:
        life events are evictable, so phase and pause state ride the
        connect topo instead."""
        return {
            "session": self._token,
            "phase": self.session.phase.value,
            "paused": self.session.paused,
            "run_id": self.session.run_id,
        }

    async def _handler(self, connection) -> None:
        self._connections.add(connection)
        try:
            await connection.send(encode(self.store.connect_frames(self._live_state())))
            async for message in connection:
                try:
                    self._handle_uplink(message)
                except Exception as error:
                    # One unhandled message costs its own reply, never the
                    # connection: a browser that loses its socket also
                    # loses the console it was reading.
                    self._reject(f"uplink failed: {error}")
        except Exception:
            # Everything else here is transport: a tab that navigated
            # away, slept, or missed the keepalive deadline ends its
            # connection, which is not a server fault to report.
            pass
        finally:
            self._connections.discard(connection)

    def _handle_uplink(self, message) -> None:
        if isinstance(message, bytes):
            message = message.decode("utf-8", errors="replace")
        try:
            uplink = parse_uplink(message)
        except UplinkError as error:
            self._reject(str(error))
            return
        if uplink.topic not in SUPPORTED_UPLINK:
            # Recognised but deferred: answer explicitly so the UI
            # degrades visibly instead of losing the command.
            self.store.publish(
                Topic.LIFE,
                Kind.EVENT,
                {"phase": "unsupported", "topic": uplink.topic.value},
            )
            return
        if uplink.topic is Topic.UART:
            reason = self.session.send_bytes(decode_bytes(str(uplink.data.get("bytes", ""))))
            if reason is not None:
                self._reject(f"uart: {reason}")
            return
        if uplink.topic is Topic.QMP:
            command = str(uplink.data.get("cmd", ""))
            if command not in ("stop", "cont"):
                self._reject(f"qmp: unknown cmd {command!r}")
            elif self.session.phase is not Phase.RUNNING or self.session.surfaces is None:
                self._reject(f"qmp: session is {self.session.phase.value}")
            else:
                self.spawn(self._halt_command(command))
            return
        demo = uplink.data.get("demo")
        if not demo:
            self._reject("target: missing demo")
            return
        if self.session.phase in (Phase.BUILDING, Phase.VERIFYING):
            # Selects queue on the session lock; accepting one per click
            # would replay every impatient click as a build+teardown.
            self._reject(f"target: session is {self.session.phase.value}")
            return
        variant = uplink.data.get("variant")
        self.spawn(
            self.session.select(
                Target(
                    demo=str(demo),
                    variant=None if variant is None else str(variant),
                    verify=bool(uplink.data.get("verify", False)),
                )
            )
        )

    def _reject(self, reason: str) -> None:
        # Window only: a flood of bad uplinks must not evict the replay
        # history every future connection depends on.
        self.store.publish(
            Topic.LIFE,
            Kind.EVENT,
            {"phase": "uplink-rejected", "reason": reason},
            replay=False,
        )

    async def _halt_command(self, command: str) -> None:
        """Pause = QMP stop + a per-CPU register sweep; the machine stays
        stopped (virtual clock frozen) until the resume command."""
        if self._halting:
            self._reject("qmp: inspection in progress")
            return
        self._halting = True
        try:
            surfaces = self.session.surfaces
            inspector = halt.HaltInspector(surfaces.qmp_path, surfaces.gdb_path)
            loop = asyncio.get_running_loop()
            if command == "stop":
                data = await loop.run_in_executor(None, inspector.pause)
                self.session.paused = True
                # Same data shape as the S-layer topics: the UI panels
                # read every snapshot's payload from "values".
                self.store.publish(
                    Topic.SYSREG, Kind.SNAPSHOT, {"values": data}, src=Src.HALT
                )
                self.store.publish(Topic.LIFE, Kind.EVENT, {"phase": "paused"})
            else:
                await loop.run_in_executor(None, inspector.resume)
                self.session.paused = False
                self.store.publish(Topic.LIFE, Kind.EVENT, {"phase": "resumed"})
        except Exception as error:
            # This coroutine is the request boundary: any protocol fault
            # (bad JSON, corrupt RSP hex, a missing XML attribute) must
            # become a reply, not an unretrieved task exception.
            self._reject(f"qmp: {error}")
        finally:
            self._halting = False

    async def _poll_loop(self) -> None:
        """Publish S-layer snapshots while a run is live.

        The provider is rebuilt per run (a rebuild moves symbols) and its
        construction — one full DWARF walk — happens off the loop. RAM
        backend races at startup retry silently; any other fault ends
        this run's S layer, is reported once, and never kills the loop.
        """
        provider = None
        poller = None
        run_id = None
        try:
            while True:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                session = self.session
                if (
                    session.phase is not Phase.RUNNING
                    or session.elf_path is None
                    or session.surfaces is None
                ):
                    continue
                current = session.run_id
                try:
                    if current != run_id:
                        if provider is not None:
                            provider.close()
                            provider = poller = None
                        shm_path = session.surfaces.shm_path
                        # Cheap gate: no per-retry DWARF walk while QEMU
                        # is still creating and sizing the backend.
                        if not shm_path.exists() or shm_path.stat().st_size == 0:
                            continue
                        built = await asyncio.get_running_loop().run_in_executor(
                            None, snapshot.ElfRamProvider, session.elf_path, shm_path
                        )
                        if session.run_id != current:
                            # A restart landed mid-build: this provider
                            # maps the previous run's RAM file.
                            built.close()
                            continue
                        provider = built
                        poller = snapshot.SnapshotPoller(provider)
                        run_id = current
                    if poller is None:
                        continue
                    for obs, value in poller.tick():
                        self.store.publish(
                            obs.topic,
                            Kind.SNAPSHOT,
                            {"values": value},
                            src=Src.SNAP,
                        )
                except FileNotFoundError:
                    continue  # the backend vanished mid-step; retry next tick
                except Exception as error:
                    self.store.publish(
                        Topic.LIFE,
                        Kind.EVENT,
                        {"phase": "snapshot-unavailable", "error": str(error)},
                    )
                    if provider is not None:
                        provider.close()
                    provider = poller = None
                    run_id = current
        finally:
            if provider is not None:
                provider.close()

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_INTERVAL_SECONDS)
            if not self._connections:
                # Leave frames in the window: the first joiner gets them
                # on the next flush instead of a silent discard.
                continue
            frames = self.store.drain()
            if not frames:
                continue
            payload = encode(frames)
            await asyncio.gather(
                *(self._send(connection, payload) for connection in tuple(self._connections))
            )

    async def _send(self, connection, payload: str) -> None:
        # One stalled client (suspended laptop, throttled tab) must not
        # hold every other client's frames hostage; a second of
        # backpressure forfeits the connection.
        try:
            await asyncio.wait_for(connection.send(payload), timeout=1.0)
        except Exception:
            self._connections.discard(connection)
            try:
                await asyncio.wait_for(connection.close(code=1011), timeout=1.0)
            except Exception:
                pass  # the transport is already beyond a clean close


async def _serve_forever(*, host: str, port: int, target: Target | None, ui_root: Path) -> None:
    if not ui_root.is_dir():
        raise SystemExit(f"[workbench] UI root missing: {ui_root}")
    surfaces = make_surfaces()
    bridge = Bridge(ui_root=ui_root, surfaces=surfaces)
    # A supervisor's SIGTERM must walk the same teardown as Ctrl-C, or
    # QEMU (its own session, immune to the terminal) outlives the bridge
    # with a gigabyte of tmpfs pinned.
    asyncio.get_running_loop().add_signal_handler(
        signal.SIGTERM, asyncio.current_task().cancel
    )
    try:
        # Topology first: a client racing the startup must never replay
        # an empty world.
        bridge.store.set_topology(initial_topology())
        await bridge.open(host, port)
        print(f"[workbench] serving http://{host}:{bridge.port}/ (WebSocket on {WS_PATH})")
        if target is not None:
            bridge.spawn(bridge.session.select(target))
        await asyncio.Future()
    finally:
        await bridge.close()
        surfaces.release()


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
    target: Target | None = None,
    ui_root: Path | None = None,
) -> int:
    try:
        asyncio.run(
            _serve_forever(
                host=host,
                port=port,
                target=target,
                ui_root=ui_root or config.WORKBENCH_UI_DIR,
            )
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass  # Ctrl-C and SIGTERM both unwound through the finally
    except OSError as error:
        # The bind is the only OS surface before the loop settles; a
        # taken port deserves one line, not a traceback.
        print(f"nova workbench: cannot serve on {host}:{port}: {error}", file=sys.stderr)
        return 2
    return 0
