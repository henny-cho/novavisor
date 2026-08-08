"""The wire contract: one envelope shape, one serializer, one time axis.

Every WebSocket message is a JSON *array* of envelopes (one batch
window), and `encode` is the only serializer, so no code path can leak
a bare object the UI would misparse. Timestamps are session-monotonic
nanoseconds anchored at bridge start; a hardware-counter mapping can
later slot in behind the same `Clock.now` call without touching users.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum

PROTOCOL_VERSION = 1

# Columns a window request may ask to be answered in. A limit on a
# request field is part of the contract, not an implementation detail of
# whoever enforces it: it lives here and rides the topology, so the two
# clients ask within it instead of each carrying a copy of the number to
# drift from.
MAX_BUCKETS = 8192


class Topic(StrEnum):
    # downlink
    TOPO = "topo"
    CONSOLE = "console"
    EV = "ev"
    LIFE = "life"
    VERIFY = "verify"
    SYSREG = "sysreg"
    TRACE = "trace"
    # uplink
    UART = "uart"
    TARGET = "target"
    # The machine's stop and its advance. Named for what it owns rather
    # than the socket it once used: QMP no longer holds the stop.
    HALT = "halt"
    # Where in the run the reader is looking. Only a replay can answer
    # it: a live machine's "now" is the only point it has.
    CURSOR = "cursor"
    CMD = "cmd"
    PROBE = "probe"


DOWNLINK = frozenset(
    {Topic.TOPO, Topic.CONSOLE, Topic.EV, Topic.LIFE, Topic.VERIFY, Topic.SYSREG, Topic.TRACE}
)
# `trace` travels both ways. A second topic for "asking about traces"
# would double SUPPORTED_UPLINK, the validation and the documentation to
# say the same word twice; the Kind already distinguishes a request from
# what the bridge sends unasked.
UPLINK = frozenset(
    {Topic.UART, Topic.TARGET, Topic.HALT, Topic.CMD, Topic.PROBE, Topic.TRACE, Topic.CURSOR}
)
# Recognised-but-deferred uplink topics are answered explicitly instead
# of being dropped, so the UI degrades visibly.
SUPPORTED_UPLINK = frozenset(
    {Topic.UART, Topic.TARGET, Topic.HALT, Topic.TRACE, Topic.CURSOR}
)


class Kind(StrEnum):
    SNAPSHOT = "snapshot"
    DELTA = "delta"
    EVENT = "event"


class Src(StrEnum):
    SERIAL = "serial"
    BRIDGE = "bridge"
    SNAP = "S"
    TRACE = "T"
    HALT = "H"


class Clock:
    """Session-monotonic nanoseconds; injectable for tests."""

    def __init__(self, monotonic_ns: Callable[[], int] = time.monotonic_ns):
        self._monotonic_ns = monotonic_ns
        self._anchor = monotonic_ns()

    def now(self) -> int:
        return self._monotonic_ns() - self._anchor


class Envelopes:
    """Envelope factory owning the connection-independent sequence."""

    def __init__(self, clock: Clock):
        self._clock = clock
        self._seq = 0

    def make(
        self,
        topic: Topic | str,
        kind: Kind | str,
        data: dict,
        *,
        src: Src | str = Src.BRIDGE,
        ts: int | None = None,
    ) -> dict:
        # Every field here takes a string as readily as its enum, and
        # for two different reasons. S-layer topics come from the
        # observation manifest, where the fixed enum covers only the
        # structural ones. `kind` and `src` come, in a replay, from
        # whatever the run that made the recording wrote — and coercing
        # an unfamiliar one into this build's enum would raise, killing
        # the connection over a field the reader never looked at. What a
        # recording says about itself travels; nothing here rewrites it.
        #
        # `ts` is given only when the moment being published is not now:
        # a replayed frame happened when the recording says it did, and
        # stamping it with this process's clock would put a run from
        # yesterday on the reader's screen as if it were live. The seq
        # is never given, because it belongs to this connection's
        # ordering and not to the run.
        self._seq += 1
        return {
            "v": PROTOCOL_VERSION,
            "seq": self._seq,
            "topic": topic.value if isinstance(topic, Topic) else topic,
            "kind": kind.value if isinstance(kind, Kind) else kind,
            "ts": self._clock.now() if ts is None else ts,
            "src": src.value if isinstance(src, Src) else src,
            "data": data,
        }


def encode(frames: Iterable[dict]) -> str:
    return json.dumps(list(frames), separators=(",", ":"), ensure_ascii=False)


class UplinkError(ValueError):
    """Client fault: reported back over the socket, never fatal."""


@dataclass(frozen=True)
class Uplink:
    topic: Topic
    data: dict


def parse_uplink(text: str) -> Uplink:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise UplinkError(f"malformed JSON: {error}") from error
    if not isinstance(payload, dict):
        raise UplinkError("uplink must be a JSON object")
    topic = payload.get("topic")
    values = {candidate.value: candidate for candidate in UPLINK}
    if topic not in values:
        raise UplinkError(f"unknown uplink topic: {topic!r}")
    data = payload.get("data", {})
    if not isinstance(data, dict):
        raise UplinkError("uplink data must be a JSON object")
    return Uplink(values[topic], data)


def decode_bytes(value: str) -> bytes:
    """Uplink `uart` payloads carry text; control characters arrive as
    their code points (Ctrl-T is "\\u0014"), so UTF-8 is the inverse."""
    return value.encode("utf-8")
