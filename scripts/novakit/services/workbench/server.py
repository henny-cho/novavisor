"""The socket edge: one port serves the UI and fans out frames.

Only this module touches websockets — lazily, mirroring the optional
YAML import — and owns the flush cadence. Everything it forwards is a
store frame produced elsewhere, so the wire content stays testable
without a socket.
"""

from __future__ import annotations

import asyncio
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
        task.add_done_callback(self._tasks.discard)

    async def close(self) -> None:
        if self._flusher is not None:
            self._flusher.cancel()
        for task in tuple(self._tasks):
            task.cancel()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        await self.session.stop()

    async def _handler(self, connection) -> None:
        self._connections.add(connection)
        try:
            await connection.send(encode(self.store.connect_frames()))
            async for message in connection:
                self._handle_uplink(message)
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
        self.store.publish(Topic.LIFE, Kind.EVENT, {"phase": "uplink-rejected", "reason": reason})

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
                # Same data shape as the S-layer topics: the UI panels
                # read every snapshot's payload from "values".
                self.store.publish(
                    Topic.SYSREG, Kind.SNAPSHOT, {"values": data}, src=Src.HALT
                )
                self.store.publish(Topic.LIFE, Kind.EVENT, {"phase": "paused"})
            else:
                await loop.run_in_executor(None, inspector.resume)
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
        backend races at startup retry silently; resolution errors are
        permanent for the run and reported once.
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
                if session.run_id != run_id:
                    if provider is not None:
                        provider.close()
                        provider = poller = None
                    loop = asyncio.get_running_loop()
                    try:
                        provider = await loop.run_in_executor(
                            None,
                            snapshot.ElfRamProvider,
                            session.elf_path,
                            session.surfaces.shm_path,
                        )
                    except (FileNotFoundError, ValueError):
                        continue  # QEMU has not sized the backend yet
                    except (KeyError, SystemExit) as error:
                        self.store.publish(
                            Topic.LIFE,
                            Kind.EVENT,
                            {"phase": "snapshot-unavailable", "error": str(error)},
                        )
                        run_id = session.run_id
                        continue
                    poller = snapshot.SnapshotPoller(provider)
                    run_id = session.run_id
                if poller is None:
                    continue
                for obs, value in poller.tick():
                    self.store.publish(
                        obs.topic,
                        Kind.SNAPSHOT,
                        {"values": value},
                        src=Src.SNAP,
                    )
        finally:
            if provider is not None:
                provider.close()

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_INTERVAL_SECONDS)
            frames = self.store.drain()
            if not frames or not self._connections:
                continue
            payload = encode(frames)
            for connection in tuple(self._connections):
                try:
                    await connection.send(payload)
                except Exception:
                    self._connections.discard(connection)


async def _serve_forever(*, host: str, port: int, target: Target | None, ui_root: Path) -> None:
    surfaces = make_surfaces()
    bridge = Bridge(ui_root=ui_root, surfaces=surfaces)
    try:
        await bridge.open(host, port)
        bridge.store.set_topology(initial_topology())
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
    except KeyboardInterrupt:
        pass
    return 0
