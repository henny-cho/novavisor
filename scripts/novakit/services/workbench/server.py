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
from . import static
from .protocol import (
    SUPPORTED_UPLINK,
    Clock,
    Envelopes,
    Kind,
    Topic,
    UplinkError,
    decode_bytes,
    encode,
    parse_uplink,
)
from .session import Deps, Session, Surfaces, Target, initial_topology, make_surfaces
from .store import StateStore

FLUSH_INTERVAL_SECONDS = 0.05
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
