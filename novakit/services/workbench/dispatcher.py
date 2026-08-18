"""Uplink request dispatching and query lifecycle management.

Handles uplink frame parsing, preconditions, query concurrency slotting,
and cancellation semantics for the workbench wire protocol.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from .protocol import (
    ProtocolError,
    Topic,
    UplinkError,
    parse_uplink,
)
from .session import Phase

if TYPE_CHECKING:
    from .server import Bridge


class Needs(StrEnum):
    """What a handler must be given before it can act, and what the
    reader is told when it is not there.
    """

    NOTHING = ""
    MACHINE = "this is a replay; there is no machine to drive"
    RUNNING = "session is {phase}"
    REPLAY = "only a replay can be moved in time"


@dataclass(frozen=True)
class Handler:
    """One uplink topic: what answers it, and what it needs to."""

    topic: Topic
    call: Callable[[Bridge, Request], object]
    needs: Needs = Needs.NOTHING
    query: bool = False


@dataclass(frozen=True)
class Request:
    """One identified uplink and the connection that owns its reply."""

    connection: object | None
    topic: Topic
    data: dict
    request_id: str


@dataclass
class QuerySlot:
    """The running question and the only newer one worth retaining."""

    active: Request
    replacement: Request | None = None


class QueryRejected(ValueError):
    """A valid question the bridge cannot answer."""


class QueryCancelled(RuntimeError):
    """A question whose run disappeared while its answer was built."""


class Dispatcher:
    """Manages uplink routing, query concurrency, and cancellation."""

    def __init__(
        self,
        handlers: tuple[Handler, ...],
        reject_fn: Callable[[str, str | None], None],
        cancel_fn: Callable[[Request, str], None],
        spawn_fn: Callable[[object], None],
    ) -> None:
        self.handlers = handlers
        self.by_topic = {h.topic: h for h in handlers}
        self.uplink_topics = frozenset(h.topic for h in handlers)
        self._reject = reject_fn
        self._cancel_pub = cancel_fn
        self._spawn = spawn_fn
        self._queries: dict[tuple[object, Topic], QuerySlot] = {}

    def disconnect_queries(self, connection: object) -> None:
        for key in [k for k in self._queries if k[0] is connection]:
            del self._queries[key]

    def request_live(
        self,
        request: Request,
        closing: bool,
        connections: set[object],
    ) -> bool:
        return not closing and (
            request.connection is None or request.connection in connections
        )

    def reject_query(
        self,
        request: Request,
        reason: str,
        closing: bool,
        connections: set[object],
    ) -> None:
        if self.request_live(request, closing, connections):
            self._reject(f"{request.topic.value}: {reason}", request.request_id)

    def cancel_query(
        self,
        request: Request,
        reason: str,
        closing: bool,
        connections: set[object],
    ) -> None:
        if self.request_live(request, closing, connections):
            self._cancel_pub(request, reason)

    def unmet(
        self,
        needs: Needs,
        phase: Phase,
        surfaces: object | None,
        is_replay: bool,
    ) -> str | None:
        """Why this handler cannot act on this session, or None."""
        if needs is Needs.MACHINE:
            refused = is_replay
        elif needs is Needs.RUNNING:
            refused = phase is not Phase.RUNNING or surfaces is None
        elif needs is Needs.REPLAY:
            refused = not is_replay
        else:
            refused = False
        return needs.value.format(phase=phase.value) if refused else None

    def schedule_query(
        self,
        bridge: Bridge,
        handler: Handler,
        request: Request,
        closing: bool,
        connections: set[object],
    ) -> None:
        key = (request.connection, request.topic)
        slot = self._queries.get(key)
        if slot is None:
            self._queries[key] = QuerySlot(request)
            self._spawn(self._run_query(bridge, handler, request, closing, connections))
            return
        if slot.replacement is not None:
            self._reject(
                f"{request.topic.value}: superseded by a newer request",
                slot.replacement.request_id,
            )
        slot.replacement = request

    async def _run_query(
        self,
        bridge: Bridge,
        handler: Handler,
        request: Request,
        closing: bool,
        connections: set[object],
    ) -> None:
        try:
            await handler.call(bridge, request)
        except QueryCancelled as error:
            self.cancel_query(request, str(error), closing, connections)
        except QueryRejected as error:
            self.reject_query(request, str(error), closing, connections)
        except Exception as error:
            self.reject_query(request, f"internal failure: {error}", closing, connections)
        finally:
            self.finish_query(bridge, handler, request, closing, connections)

    def finish_query(
        self,
        bridge: Bridge,
        handler: Handler,
        request: Request,
        closing: bool,
        connections: set[object],
    ) -> None:
        key = (request.connection, request.topic)
        slot = self._queries.get(key)
        if slot is None or slot.active is not request:
            return
        replacement = slot.replacement
        del self._queries[key]
        if replacement is not None and self.request_live(replacement, closing, connections):
            self._queries[key] = QuerySlot(replacement)
            self._spawn(self._run_query(bridge, handler, replacement, closing, connections))

    def handle_uplink(
        self,
        bridge: Bridge,
        message: str | bytes,
        connection: object | None,
        phase: Phase,
        surfaces: object | None,
        is_replay: bool,
        closing: bool,
        connections: set[object],
    ) -> None:
        if isinstance(message, bytes):
            try:
                message = message.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ProtocolError(f"uplink must be UTF-8: {error}") from error
        try:
            uplink = parse_uplink(message, self.uplink_topics)
        except UplinkError as error:
            self._reject(str(error), error.request_id)
            return
        handler = self.by_topic[uplink.topic]
        request = Request(connection, uplink.topic, uplink.data, uplink.request_id)
        unmet_reason = self.unmet(handler.needs, phase, surfaces, is_replay)
        if unmet_reason is not None:
            self._reject(f"{handler.topic.value}: {unmet_reason}", request.request_id)
            return
        if handler.query:
            self.schedule_query(bridge, handler, request, closing, connections)
            return
        try:
            handler.call(bridge, request)
        except Exception as error:
            self._reject(f"{handler.topic.value}: {error}", request.request_id)
