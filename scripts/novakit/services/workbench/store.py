"""Frame batching and late-joiner state, free of any socket.

Everything the bridge learns flows through `StateStore.publish`; the
flush loop drains one `FrameWindow` per batch interval and is the only
socket writer. A client may see a frame twice across connect replay and
the next flush — the monotonic `seq` lets it drop duplicates.
"""

from __future__ import annotations

from collections import deque

from .protocol import Envelopes, Kind, Src, Topic


class FrameWindow:
    """Envelopes pending between two flushes, with a bounded footprint.

    On overflow the oldest console frames go first — they are the bulk
    and the least stateful — and one notice records how many were lost.
    """

    def __init__(self, max_frames: int = 4096):
        self._frames: deque[dict] = deque()
        self._max_frames = max_frames
        self.dropped = 0

    def add(self, frame: dict) -> None:
        self._frames.append(frame)
        if len(self._frames) <= self._max_frames:
            return
        for index, queued in enumerate(self._frames):
            if queued["topic"] == Topic.CONSOLE.value:
                del self._frames[index]
                break
        else:
            self._frames.popleft()
        self.dropped += 1

    def drain(self) -> list[dict]:
        frames = list(self._frames)
        self._frames.clear()
        return frames


class StateStore:
    def __init__(
        self,
        envelopes: Envelopes,
        window: FrameWindow | None = None,
        backlog_limit: int = 500,
    ):
        self._envelopes = envelopes
        self.window = window if window is not None else FrameWindow()
        self._topology: dict = {}
        # Recent history replayed to late joiners, console included so a
        # fresh browser is not blank until the next event.
        self._backlog: deque[dict] = deque(maxlen=backlog_limit)

    def publish(
        self,
        topic: Topic | str,
        kind: Kind,
        data: dict,
        *,
        src: Src = Src.BRIDGE,
        replay: bool = True,
    ) -> dict:
        """`replay=False` keeps a frame out of the connect backlog — for
        per-request noise (rejections) that must not evict history."""
        frame = self._envelopes.make(topic, kind, data, src=src)
        self.window.add(frame)
        if replay:
            self._backlog.append(frame)
        return frame

    def set_topology(self, data: dict) -> dict:
        self._topology = data
        return self.publish(Topic.TOPO, Kind.SNAPSHOT, data)

    def drain(self) -> list[dict]:
        frames = self.window.drain()
        if self.window.dropped:
            notice = self._envelopes.make(
                Topic.LIFE,
                Kind.EVENT,
                {"phase": "frames-dropped", "count": self.window.dropped},
            )
            # Late joiners replay the backlog; the loss must be part of
            # the history it punched a hole into.
            self._backlog.append(notice)
            frames.append(notice)
            self.window.dropped = 0
        return frames

    def connect_frames(self) -> list[dict]:
        """Replay for a new connection: topology first, then history.

        The topo envelope is built fresh (not published) so replaying to
        one client does not re-broadcast to every other one.
        """
        topo = self._envelopes.make(Topic.TOPO, Kind.SNAPSHOT, self._topology)
        return [topo, *self._backlog]
