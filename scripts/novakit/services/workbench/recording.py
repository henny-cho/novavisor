"""Writing a run down, and reading it back.

Every fact the browser learns arrives as an envelope, and every envelope
comes out of one function. That single funnel is what makes recording a
tee rather than a second account of the run: there is no path a value
can take to the screen that this does not see, so a recording cannot be
missing something a live viewer had.

It is also more complete than any viewer. The frame window drops console
frames when a batch overruns and says how many; the tee sits ahead of
that window, so what a throttled tab lost is still on disk.

Two files because there are two kinds of data and each already has a
natural shape. Envelopes are JSON and go one per line; drained records
are the firmware's own 32 bytes and go as bytes. Inventing a container
to hold both would be a third format to keep true, and the reader would
have to unpick it before it could do anything the two files already
allow directly.

    run-2608072210-13_linux/
      meta.json   what this is: demo, run, clock, board, totals
      wire.jsonl  one envelope per line, in publish order
      trace.bin   drained records, arrival order, gaps included

No keyframes. The obvious index — every N frames, the topic values as of
then — is a cache that can disagree with the stream it summarises, and
the reader can fold exactly the same thing out of the stream itself at
load. A file with one record kind fewer cannot go stale in that way.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import trace

VERSION = 1
META = "meta.json"
WIRE = "wire.jsonl"
RECORDS = "trace.bin"


class Unreadable(RuntimeError):
    """This directory is not a recording this reader understands.

    Raised rather than worked around: a version skew means the writer
    and this file disagree about the layout, and replaying anyway would
    put plausible-looking nonsense on the same screen the live bridge
    uses — which is the one place it must not appear.
    """


class Recorder:
    """A run, being written down.

    `publish()` is synchronous and sits on the bridge's only thread, so
    nothing here touches the disk: frames go into a list and records
    into a buffer, and the flush loop that was already waking every 50
    ms writes them. The recording is behind the live view by at most one
    batch, which is the same amount the browser is behind it.
    """

    def __init__(self, directory: Path, meta: dict | None = None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._meta = dict(meta or {})
        self._wire = (self.directory / WIRE).open("w", encoding="utf-8")
        self._records = (self.directory / RECORDS).open("wb")
        self._pending: list[str] = []
        self._bytes = bytearray()
        self.frames = 0
        self.records = 0
        self._started = datetime.now(UTC).isoformat(timespec="seconds")

    def note(self, **fields) -> None:
        """Facts about the run that are not known when it starts.

        The clock its timestamps are in is the one that matters: it
        comes off the region header, which nobody has read yet at the
        moment the recorder opens. Merged rather than published as a
        frame, because a reader needs it before it can interpret the
        first frame.
        """
        self._meta.update(fields)

    def frame(self, frame: dict) -> None:
        self._pending.append(json.dumps(frame, ensure_ascii=False))
        self.frames += 1

    def drained(self, records: list[trace.Record]) -> None:
        at = len(self._bytes)
        self._bytes.extend(bytes(len(records) * trace.REC_SIZE))
        for index, record in enumerate(records):
            trace.pack_into(self._bytes, at + index * trace.REC_SIZE, record)
        self.records += len(records)

    def flush(self) -> None:
        if self._pending:
            self._wire.write("\n".join(self._pending) + "\n")
            self._wire.flush()
            self._pending.clear()
        if self._bytes:
            self._records.write(self._bytes)
            self._records.flush()
            self._bytes.clear()

    def close(self) -> dict:
        """Finish the files and write what this run turned out to be.

        The totals go in last because they are not known until now, and
        a meta written up front would be a promise about a run still
        happening.
        """
        self.flush()
        self._wire.close()
        self._records.close()
        meta = {
            "v": VERSION,
            "started": self._started,
            "frames": self.frames,
            "records": self.records,
            **self._meta,
        }
        (self.directory / META).write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
        return meta

    def sizes(self) -> dict[str, int]:
        return {
            name: (self.directory / name).stat().st_size
            for name in (META, WIRE, RECORDS)
            if (self.directory / name).exists()
        }


@dataclass(frozen=True)
class Recording:
    """A run, read back."""

    directory: Path
    meta: dict
    frames: list[dict] = field(default_factory=list)
    records: list[trace.Record] = field(default_factory=list)


def load(directory: Path) -> Recording:
    """Everything a recording holds, in the order it was written.

    Read whole rather than streamed: the point of a recording is to be
    seeked around in, and a stream cannot answer "go to the middle"
    without holding the middle anyway. A twenty-minute run is a few
    hundred megabytes, which is what a bridge already holds live.
    """
    directory = Path(directory)
    meta_path = directory / META
    if not meta_path.is_file():
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
            # A half-written last line is a run that was killed, which
            # is an ordinary way for a recording to end. The same damage
            # anywhere earlier is a corrupt file and says so.
            if number != len(lines):
                raise Unreadable(f"{wire_path}:{number}: {error}") from error

    blob = (directory / RECORDS).read_bytes() if (directory / RECORDS).is_file() else b""
    records = [
        trace.unpack_from(blob, at)
        for at in range(0, len(blob) - len(blob) % trace.REC_SIZE, trace.REC_SIZE)
    ]
    return Recording(directory=directory, meta=meta, frames=frames, records=records)
