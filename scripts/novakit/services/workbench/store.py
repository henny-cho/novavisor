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
        on_frame=None,
    ):
        self._envelopes = envelopes
        self.window = window if window is not None else FrameWindow()
        self._topology: dict = {}
        # Recent history replayed to late joiners, console included so a
        # fresh browser is not blank until the next event.
        self._backlog: deque[dict] = deque(maxlen=backlog_limit)
        # An observer wanting every frame, called ahead of the window
        # that may drop them. A callable rather than a recorder, so this
        # file stays as free of the recording layer as it is of sockets.
        self._on_frame = on_frame

    def publish(
        self,
        topic: Topic | str,
        kind: Kind,
        data: dict,
        *,
        src: Src | str = Src.BRIDGE,
        replay: bool = True,
        ts: int | None = None,
    ) -> dict:
        """Mint an envelope and broadcast it to every client.

        `replay=False` keeps it out of the connect backlog — for
        per-request noise (rejections) that must not evict history.

        `ts` names the moment when it is not now: a recorded frame
        happened when the recording says it did.
        """
        frame = self.stamp(topic, kind, data, src=src, ts=ts)
        # Ahead of the window, which sheds console frames on overrun: an
        # observer of everything must not be given a client's view.
        if self._on_frame is not None:
            self._on_frame(frame)
        self.window.add(frame)
        if replay:
            self._backlog.append(frame)
        return frame

    def stamp(
        self,
        topic: Topic | str,
        kind: Kind | str,
        data: dict,
        *,
        src: Src | str = Src.BRIDGE,
        ts: int | None = None,
    ) -> dict:
        """Mint an envelope and hand it straight back, announcing nothing.

        For a caller delivering to one socket itself. Publishing a whole
        recording instead pushed it through the broadcast window, which
        shed thousands of frames, re-sent what it kept on the next flush,
        and reported the shedding as the bridge having fallen behind.
        """
        return self._envelopes.make(topic, kind, data, src=src, ts=ts)

    @property
    def topology(self) -> dict:
        """The world as last published. Read-only to callers; a late
        joiner is replayed from exactly this."""
        return self._topology

    def adopt_topology(self, data: dict) -> None:
        """Set the world without announcing it.

        For startup, before the socket is open: there is nobody to
        announce it to, and the frame would sit in the backlog to be
        replayed after every future connect's fresh topology — an older
        description of the world arriving second. In a replay the stale
        copy carries no phase, so it re-enables the controls the fresh
        one had just disabled.
        """
        self._topology = data

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

    def connect_frames(self, live_state: dict | None = None) -> list[dict]:
        """Replay for a new connection: fresh topology, then history.

        Published rather than privately minted, so the sequence number it
        consumes is one every other client also receives. It carries the
        connect-time session state (phase, pause, run identity) that a
        late joiner cannot recover from evictable life events. The
        backlog is captured first, so the fresh topology appears once.

        Kept out of the backlog: it describes the session for the one
        client that asked, every later connect gets its own, and stored
        it would arrive after the fresh copy that replaced it.
        """
        history = list(self._backlog)
        topo = self.publish(
            Topic.TOPO, Kind.SNAPSHOT, {**self._topology, **(live_state or {})}, replay=False
        )
        return [topo, *history]
