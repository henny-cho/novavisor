"""The only place that spawns a process and captures its output.

pexpect lives here and nowhere else: expect.py holds the decision logic and
stays testable against a fake child.
"""

from __future__ import annotations

import sys
import time
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


def observe(
    scenario: expect.Scenario,
    *,
    stream,
    clock: Callable[[], float] | None = None,
    on_match: Callable[[expect.PatternMatch], None] | None = None,
) -> Run:
    """Run one scenario to its outcome, capturing everything the child prints.

    The clock is read here rather than defaulted in the signature so a test
    can substitute one.
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
        )
    except (Exception, SystemExit) as exc:
        return Run(expect.spawn_failure(exc), capture)
    child.logfile_read = capture
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
