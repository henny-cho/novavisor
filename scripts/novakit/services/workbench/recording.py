"""Writing a run to disk, and reading it back.

The recorder is a tee on `StateStore.publish`, the single function every
downlink envelope passes through, and it sits ahead of the frame window
that sheds console frames on overrun. So the file holds everything the
wire carried, including what a throttled client never received.

One directory per run, numbered in the order they were recorded:

    DIR/
      run-1/
        meta.json   what this is: version, demo, clock, board, complete
        wire.jsonl  one envelope per line, in publish order
        trace.bin   drained records, arrival order, gap records included

Two files because envelopes are JSON and records are the firmware's own
32 bytes; a single container would be a third format to maintain.

The file holds no derived data — no keyframe index, no frame or record
totals. `fold()` rebuilds the index at load and `len()` answers the
totals, so there is nothing stored that can disagree with the stream.
"""

from __future__ import annotations

import bisect
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import trace
from .protocol import Topic

# 2: meta gained `complete` and dropped the frame and record totals.
VERSION = 2
META = "meta.json"
WIRE = "wire.jsonl"
RECORDS = "trace.bin"
# The topic carrying the world, spelled once. This file distinguishes
# readings from the world in three places.
TOPO = Topic.TOPO.value


class Unreadable(RuntimeError):
    """This directory is not a recording this reader understands.

    Raised rather than decoded on a best-effort basis: a version skew
    means writer and reader disagree about the layout, and the result
    would appear on the same screen the live bridge uses.
    """


def _named(world: dict | None) -> dict:
    """The demo and variant a topology frame names, or nothing.

    Called when the recorder learns which run it is following, which is
    the only moment the world and the run agree: a demo's topology is
    published while the previous run is still current, so neither the
    launch target nor the last topology in a file names the run reliably.

    Both fields travel together — a variant belongs to the demo beside it.
    """
    data = (world or {}).get("data") or {}
    return {"demo": data["demo"], "variant": data.get("variant")} if data.get("demo") else {}


class Recorder:
    """A run being written down.

    `publish()` is synchronous on the bridge's only thread, so nothing
    here touches the disk: frames go into a list and records into a
    buffer, and the 50 ms flush loop writes both. The file trails the
    live view by at most one batch.

    Each machine gets its own directory. Everything downstream — the
    history's bisection, the window protocol, the seek index, the
    CNTPCT-to-wire mapping — reads a recording as one monotonic stream,
    and a restart begins the machine's clock again, so two runs
    concatenated would produce a span that runs backwards and windows
    that answer with the wrong records.

    The meta is written at open and again at close, so the directory is
    loadable at every instant rather than only after a clean exit.
    """

    def __init__(self, root: Path, meta: dict | None = None):
        self.root = Path(root)
        if any(self.root.glob(f"**/{META}")):
            # Refused rather than opened with "w". The meta written at
            # open is what makes a killed recording visible here.
            raise FileExistsError(f"{self.root} already holds a recording")
        self._meta = dict(meta or {})
        self._run = 0  # the machine being recorded; 0 is "none yet"
        self._index = 0
        # The last topology frame seen, carried across a roll by _open.
        self._world: dict | None = None
        self.written: list[Path] = []
        self._open()

    def _open(self) -> None:
        self._index += 1
        self.directory = self.root / f"run-{self._index}"
        self.directory.mkdir(parents=True, exist_ok=True)
        self._wire = (self.directory / WIRE).open("w", encoding="utf-8")
        self._records = (self.directory / RECORDS).open("wb")
        self._pending: list[str] = []
        self._bytes = bytearray()
        self._started = datetime.now(UTC).isoformat(timespec="seconds")
        self._write_meta(complete=False)
        if self._world is not None:
            # A run's topology is published during the select that builds
            # it, before the launch bumps the run id this rolls on, so it
            # lands in the previous run's file and the new one opens with
            # no world at all. Re-emitted verbatim: minting a fresh
            # envelope would consume a sequence number that live clients
            # never receive and read as a gap.
            self.frame(self._world)

    def for_run(self, run_id: int) -> None:
        """Follow the machine, rolling to a new directory on a restart.

        The first launch does not roll: the topology, the build and the
        launch itself are that run's opening, not a recording of their
        own. A machine replacing a machine does, because that is where
        the guest clock restarts.
        """
        if run_id == self._run:
            return
        if self._run:
            self.close()
            self._open()
        self._run = run_id
        self.note(run_id=run_id, **_named(self._world))

    def note(self, **fields) -> None:
        """Merge facts learned after the run started into the meta.

        The counter frequency is the one that matters: it comes off the
        trace region header, which nobody has read when the recorder
        opens, and a reader needs it before it can interpret any
        timestamp. Written through immediately, so a killed run still
        carries the clock it was stamped in.
        """
        self._meta.update(fields)
        self._write_meta(complete=False)

    def frame(self, frame: dict) -> None:
        self._pending.append(json.dumps(frame, ensure_ascii=False))
        if frame.get("topic") == TOPO:
            self._world = frame

    def drained(self, records: list[trace.Record]) -> None:
        at = len(self._bytes)
        self._bytes.extend(bytes(len(records) * trace.REC_SIZE))
        for index, record in enumerate(records):
            trace.pack_into(self._bytes, at + index * trace.REC_SIZE, record)

    def flush(self) -> None:
        if self._pending:
            self._wire.write("\n".join(self._pending) + "\n")
            self._wire.flush()
            self._pending.clear()
        if self._bytes:
            self._records.write(self._bytes)
            self._records.flush()
            self._bytes.clear()

    def _write_meta(self, *, complete: bool) -> dict:
        """Write what this recording is, as of now.

        `complete` is the only fact the two data files cannot answer: a
        killed run looks exactly like a finished one minus its last line.
        False until close() rewrites it.
        """
        meta = {
            "v": VERSION,
            "started": self._started,
            "complete": complete,
            **self._meta,
        }
        (self.directory / META).write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
        return meta

    def close(self) -> dict:
        """Finish the files and mark the run as one that ended."""
        self.flush()
        self._wire.close()
        self._records.close()
        self.written.append(self.directory)
        return self._write_meta(complete=True)

    def sizes(self) -> dict[str, int]:
        """Bytes on disk, across every run this recorder has written."""
        return {
            str(directory / name): (directory / name).stat().st_size
            for directory in self.written
            for name in (META, WIRE, RECORDS)
            if (directory / name).exists()
        }


# Frames between folded checkpoints. A seek applies at most this many
# frames from the nearest checkpoint, and a twenty-minute run holds a few
# hundred checkpoints rather than tens of thousands.
CHECKPOINT_EVERY = 256


@dataclass(frozen=True)
class Checkpoint:
    """The world as of one point in the stream."""

    at: int  # index into frames; the state is *before* applying this one
    ts: int
    topics: dict[str, dict]  # topic -> the frame's data, as last seen


def _readings(frame: dict) -> bool:
    """Whether a frame carries a topic's reading.

    The topology is excluded: it is the world, answered from the store,
    and folding it in would put the session's phase in a panel's table.
    """
    return frame.get("kind") == "snapshot" and frame.get("topic") != "topo"


def _topics_of(frames: list[dict]) -> tuple[str, ...]:
    """Every topic this run published a reading for, in first-seen order.

    A seek needs the full set to report which topics had no value yet at
    that moment; saying nothing about them would leave their later
    readings on screen.
    """
    return tuple(dict.fromkeys(frame["topic"] for frame in frames if _readings(frame)))


def _drains_of(frames: list[dict]) -> tuple[tuple[int, int], ...]:
    """(newest CNTPCT taken in, frame timestamp) for every drain, sorted.

    Records are stamped in CNTPCT and frames in the bridge's monotonic
    clock. Every drain summary carries both, so the pairs linking them
    are already in the stream and this only collects them.
    """
    pairs = [
        (((frame.get("data") or {}).get("span") or {}).get("to", 0), frame.get("ts", 0))
        for frame in frames
        if frame.get("topic") == "trace" and frame.get("kind") == "event"
    ]
    return tuple(sorted(pairs))


@dataclass(frozen=True)
class Recording:
    """A loaded run, with the index derived from its own frames.

    Deriving in __post_init__ rather than at the call site means holding
    a Recording is holding one whose index agrees with its stream: there
    is no loader step to forget and no order of operations to get wrong.

    `marks` accepts a different checkpoint interval so a test can show
    the interval cannot change an answer. It cannot be given a different
    answer.
    """

    directory: Path
    meta: dict
    frames: list[dict] = field(default_factory=list)
    records: list[trace.Record] = field(default_factory=list)
    marks: list[Checkpoint] | None = None
    # Derived below, never passed. Fields rather than cached properties:
    # recomputing per seek cost three full scans of the run for every
    # move of the cursor.
    topics: tuple[str, ...] = ()
    drains: tuple[tuple[int, int], ...] = ()

    def __post_init__(self) -> None:
        fill = object.__setattr__  # frozen to callers; derived fields are ours
        if self.marks is None:
            fill(self, "marks", fold(self.frames))
        fill(self, "topics", _topics_of(self.frames))
        fill(self, "drains", _drains_of(self.frames))

    def wire_ts(self, cntpct: int) -> int:
        """When the bridge learned of a record stamped `cntpct`.

        The first drain whose span reached that timestamp handed it over,
        so everything published before that frame precedes it in the
        order the browser saw — which is what a cursor cuts on. No
        interpolation: the answer is a frame that exists.
        """
        at = bisect.bisect_left(self.drains, (cntpct,))
        if at < len(self.drains):
            return self.drains[at][1]
        # Newer than every drain: the end of the run.
        return self.frames[-1].get("ts", 0) if self.frames else 0

    def at(self, ts: int) -> dict[str, dict]:
        """Every topic's latest reading as of `ts`.

        Applied forward from the nearest checkpoint, so the cost is the
        checkpoint interval rather than the length of the run.
        """
        state: dict[str, dict] = {}
        start = 0
        for mark in self.marks:
            if mark.ts > ts:
                break
            state, start = dict(mark.topics), mark.at
        for index in range(start, len(self.frames)):
            frame = self.frames[index]
            if frame.get("ts", 0) > ts:
                break
            if _readings(frame):
                state[frame["topic"]] = frame
        return state


def fold(frames: list[dict], every: int = CHECKPOINT_EVERY) -> list[Checkpoint]:
    """Build the seek checkpoints by walking the stream.

    Written into the file they would be a cache of it, free to disagree
    after any edit and invisible when they do, because the summary is
    what gets read. Folded at load there is one description of the run.
    """
    marks: list[Checkpoint] = []
    state: dict[str, dict] = {}
    for index, frame in enumerate(frames):
        if index % every == 0:
            marks.append(Checkpoint(at=index, ts=frame.get("ts", 0), topics=dict(state)))
        if _readings(frame):
            state[frame["topic"]] = frame
    return marks


def _run_index(directory: Path) -> tuple[int, str]:
    """Sort key for a `run-N` directory: the number the recorder assigned.

    Names that do not fit the pattern sort first and by name, which is a
    stable answer rather than a guess.
    """
    suffix = directory.name.removeprefix("run-")
    return (int(suffix), "") if suffix.isdigit() else (-1, directory.name)


def load(directory: Path) -> Recording:
    """Read a recording whole, in the order it was written.

    Not streamed: a recording exists to be seeked around in, and
    answering "go to the middle" means holding the middle anyway. A
    twenty-minute run is a few hundred megabytes, which is what the
    bridge already holds live.
    """
    directory = Path(directory)
    meta_path = directory / META
    if not meta_path.is_file():
        # A `--record` directory holds one subdirectory per run, so what
        # a reader has in hand is as often the root as a single run.
        #
        # Ordered by the number the recorder assigned, not by mtime: a
        # recording is a thing people copy, and a copy re-stamps every
        # mtime in whatever order the directory was walked.
        runs = sorted(
            (child for child in directory.glob("run-*") if (child / META).is_file()),
            key=_run_index,
        )
        if runs:
            return load(runs[-1])
        raise Unreadable(f"{directory} holds no {META}")
    try:
        meta = json.loads(meta_path.read_text())
    except json.JSONDecodeError as error:
        raise Unreadable(f"{meta_path}: {error}") from error
    if meta.get("v") != VERSION:
        raise Unreadable(f"recording version {meta.get('v')}, expected {VERSION}")

    frames = []
    wire_path = directory / WIRE
    lines = wire_path.read_text(encoding="utf-8").splitlines() if wire_path.is_file() else []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            frames.append(json.loads(line))
        except json.JSONDecodeError as error:
            # A half-written last line is a killed run, which the meta
            # already reports. The same damage earlier is a corrupt file.
            if number != len(lines):
                raise Unreadable(f"{wire_path}:{number}: {error}") from error

    blob = (directory / RECORDS).read_bytes() if (directory / RECORDS).is_file() else b""
    records = [
        trace.unpack_from(blob, at)
        for at in range(0, len(blob) - len(blob) % trace.REC_SIZE, trace.REC_SIZE)
    ]
    return Recording(directory=directory, meta=meta, frames=frames, records=records)
