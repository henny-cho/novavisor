"""The only place that spawns a process and captures its output.

pexpect lives here and nowhere else: expect.py holds the decision logic and
stays testable against a fake child.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..core import board, proc
from . import expect


class OutputCapture:
    """Keep a bounded diagnostic tail and stream only outside CI."""

    def __init__(self, stream, max_bytes: int = 32 * 1024):
        self.stream = stream
        self.max_bytes = max_bytes
        self.tail = ""

    def write(self, data: str) -> None:
        if self.stream is not None:
            self.stream.write(data)
        encoded = (self.tail + data).encode("utf-8")
        if len(encoded) > self.max_bytes:
            encoded = encoded[-self.max_bytes:]
        # The byte window may begin in the middle of a UTF-8 sequence.
        self.tail = encoded.decode("utf-8", errors="ignore")

    def flush(self) -> None:
        if self.stream is not None:
            self.stream.flush()


def print_tail(capture: OutputCapture, *, scope: str) -> None:
    # Skip only when the console already saw it live; a file stream means
    # the failure tail is still missing from the log the reader has.
    if capture.stream is sys.stdout or not capture.tail:
        return
    print(f"[{scope}] --- QEMU output tail ---", file=sys.stderr)
    print(capture.tail, file=sys.stderr, end="" if capture.tail.endswith("\n") else "\n")


def preserve_tail(capture: OutputCapture, path: Path | None, *, scope: str) -> None:
    print_tail(capture, scope=scope)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(capture.tail, encoding="utf-8")


@dataclass(frozen=True)
class Run:
    result: expect.VerificationResult
    capture: OutputCapture


def _require_pexpect():
    try:
        import pexpect

        return pexpect
    except ImportError:
        raise SystemExit("QEMU verification requires python3-pexpect or pexpect")


class LiveSession:
    """A child kept alive for interactive use, unlike `observe`.

    The pty master is a selectable fd, so an event loop can add_reader
    it and drain with `read_available`; pexpect's expect machinery is
    never used on this child.
    """

    def __init__(self, child):
        self._child = child

    def fileno(self) -> int:
        return self._child.child_fd

    def read_available(self, size: int = 65536) -> bytes | None:
        """Bytes ready on the pty, or None once it is gone.

        Linux raises EIO on a pty master whose slave side closed, so
        both that and an empty read mean end-of-stream.
        """
        try:
            data = os.read(self._child.child_fd, size)
        except OSError:
            return None
        return data or None

    def write(self, data: bytes) -> None:
        self._child.send(data)

    def poll_exit(self) -> int | None:
        if self._child.isalive():
            return None
        if self._child.exitstatus is not None:
            return self._child.exitstatus
        signal = self._child.signalstatus
        return -signal if signal is not None else None

    def terminate(self) -> bool:
        return self._child.terminate(force=True)


def launch(argv: Sequence[str]) -> LiveSession:
    """Spawn a child on a pty and hand its lifetime to the caller.

    echo stays off: the serial consumer renders exactly what the
    firmware prints, not a pty reflection of its own input.
    """
    pexpect = _require_pexpect()
    command = [str(argument) for argument in argv]
    proc.log(command)
    return LiveSession(pexpect.spawn(command[0], command[1:], timeout=None, echo=False))


def observe(
    scenario: expect.Scenario,
    *,
    stream,
    clock: Callable[[], float] | None = None,
    on_match: Callable[[expect.PatternMatch], None] | None = None,
    on_spawn: Callable[[object], None] | None = None,
) -> Run:
    """Run one scenario to its outcome, capturing everything the child prints.

    The clock is read here rather than defaulted in the signature so a test
    can substitute one. `on_spawn` hands the child out as soon as it
    exists — the caller's only chance to terminate a run early, since
    this function blocks its thread until the scenario resolves.
    """
    pexpect = _require_pexpect()
    capture = OutputCapture(stream)
    command = [str(argument) for argument in scenario.command]
    proc.log(command)
    try:
        child = pexpect.spawn(
            command[0],
            command[1:],
            timeout=scenario.timeout_seconds,
            encoding="utf-8",
            # Firmware console bytes are not guaranteed UTF-8: SMP cores
            # interleave multibyte writes and SIGKILL cuts them anywhere.
            # One bad byte must cost one �, never the verification.
            codec_errors="replace",
        )
    except (Exception, SystemExit) as exc:
        return Run(expect.spawn_failure(exc), capture)
    child.logfile_read = capture
    if on_spawn is not None:
        on_spawn(child)
    try:
        result = expect.observe_output(
            child,
            list(scenario.expectations),
            scenario.timeout_seconds,
            clock=clock or time.monotonic,
            timeout_error=pexpect.TIMEOUT,
            eof_error=pexpect.EOF,
            on_match=on_match,
            fatal_patterns=board.FATAL_PATTERNS,
            forbidden_patterns=scenario.forbidden_patterns,
        )
    except expect.Interrupted as interrupted:
        interrupted.capture = capture
        raise
    return Run(result, capture)
