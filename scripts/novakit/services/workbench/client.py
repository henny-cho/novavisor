"""Asking a running bridge for what it holds.

The other side of the wire from `server.py`, and the only other module
that knows there is one. A terminal wanting the ordered records has to
ask the process that has them: two consumers reading the firmware's
rings with two cursors would answer differently about one run, and the
rings hold seconds where the bridge's history holds minutes.

Kept out of `trace.py` deliberately. That module reads a region of
memory and knows nothing about sockets — the boundary the automation
layer test enforces is not "one file may import websockets" but "only
the files whose job is the wire".
"""

from __future__ import annotations

import json
import sys
import time

from . import trace


def tail(port: int, seconds: float, limit: int, forever: bool) -> int:
    """The terminal twin against a running bridge's history.

    One history, two consumers. Reading the rings directly would give
    this the same two-and-a-bit seconds the browser stopped being
    limited to, and the two would then disagree about one run — same
    firmware, two cursors, different answers.

    A terminal asks for a stretch of time, not for everything: a window
    wide enough to hold more records than it was asked to enumerate
    comes back as density, which is the right answer to the browser's
    question and nothing this can print. `seconds` is that stretch, and
    a window still too busy says so rather than printing nothing.
    """
    try:
        from websockets.sync.client import connect
    except ImportError:
        print("[workbench] trace: the pinned websockets package is missing", file=sys.stderr)
        return 1

    newest = 0  # the last timestamp printed, so following never repeats one
    try:
        with connect(f"ws://127.0.0.1:{port}/ws", max_size=None) as socket:
            while True:
                span, freq = _await_span(socket)
                if span is None:
                    print("[workbench] trace: nothing recorded yet", file=sys.stderr)
                    return 1
                # From where the last line left off, or a stretch back.
                back = int(seconds * freq) if freq else span["to"] - span["from"]
                first = newest + 1 if newest else max(span["from"], span["to"] - back)
                if first <= span["to"]:
                    records = _narrow(socket, first, span["to"], limit)
                    if records:
                        newest = records[-1].ts
                        trace.print_records(records, freq, limit)
                if not forever:
                    return 0
    except (KeyboardInterrupt, ConnectionError, OSError):
        return 0
    except Exception as error:  # a bridge that went away mid-question
        print(f"[workbench] trace: {error}", file=sys.stderr)
        return 1


def _frames(socket, seconds: float = 15.0):
    """Envelopes off the socket until the deadline. The bridge batches,
    so one receive is a list of them."""
    end = time.monotonic() + seconds
    while True:
        left = end - time.monotonic()
        if left <= 0:
            return
        try:
            payload = socket.recv(timeout=left)
        except TimeoutError:
            return
        yield from json.loads(payload)


def _await_span(socket, seconds: float = 2.0):
    """The bridge's own account of what it still holds, as it stands now.

    Asked rather than assumed: the horizon belongs to the bridge, and a
    client that guessed a range would be inventing the one number the
    history publishes precisely so nobody has to.

    The newest such account, not the first to arrive. A connection is
    replayed the backlog, so the first summary a client sees describes
    the history as it was at its first record — a window of one, which
    is why asking for everything printed a single line.
    """
    latest, freq = None, 0
    for frame in _frames(socket, seconds):
        if frame.get("topic") != "trace" or frame.get("kind") != "event":
            continue
        span = frame["data"].get("span")
        if span and span.get("n") and (latest is None or span["to"] > latest["to"]):
            latest = span
            freq = span.get("freq_hz", freq)
    return latest, freq


def _narrow(socket, first: int, last: int, limit: int) -> list[trace.Record]:
    """The newest records in a stretch, narrowing until they can be sent.

    A window holding more records than the resolution asked for comes
    back as density — right for a strip of pixels, useless to a
    terminal. So the terminal does what a reader dragging the strip
    does: it asks again for less of the same stretch. Density gives the
    count, and the count says by how much to narrow, so this converges
    in a round trip or two rather than guessing.
    """
    room = max(limit, 64)
    for _ in range(6):
        records, dense = _window(socket, first, last, room)
        if not dense:
            return records
        # Aim a few times wider than the number of lines asked for, from
        # the newest end: density is uneven, so a window sized to exactly
        # `limit` lands short of it as often as not, and the printer
        # takes the newest `limit` from whatever arrives.
        width = max(1, int((last - first) * min(room, limit * 4) / dense))
        first = max(first, last - width)
    print(
        "[workbench] that stretch stays too dense to list; narrow it with --since",
        file=sys.stderr,
    )
    return []


def _window(socket, first: int, last: int, limit: int) -> tuple[list[trace.Record], int]:
    """One window: its records, or how many were too many to list.

    A response carries records or the density standing in for them. A
    terminal wants the records, so it asks for room — and when the
    answer is a density it returns the count instead of an empty list,
    because "nothing happened" and "too much happened" are not the same
    report.
    """
    socket.send(json.dumps({
        "topic": "trace",
        "data": {"op": "window", "from": first, "to": last, "buckets": max(limit, 4096)},
    }))
    for frame in _frames(socket):
        if frame.get("topic") != "trace" or frame.get("kind") != "snapshot":
            continue
        data = frame["data"]
        cols = data.get("cols")
        if cols is None:
            return [], data["window"]["n"]
        base = data["window"]["from"]
        return [
            trace.Record(base + cols["ts"][index], cols["code"][index], cols["cpu"][index],
                   cols["a"][index], cols["b"][index], cols["c"][index])
            for index in range(len(cols["ts"]))
        ], 0
    return [], 0
