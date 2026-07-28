"""Process output capture and the verification state machine.

Nothing here knows about manifests, builds, or artifact filenames: it observes
one spawned process against a list of expectations and reports the outcome.
"""

from __future__ import annotations

import sys
import time
import traceback
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Callable, Sequence

from . import config, process


def board_command(
    *,
    kernel: Path | None = None,
    bios: Path | None = None,
    secure: bool = False,
) -> list[str]:
    args = list(config.QEMU_BOARD_ARGS)
    if secure:
        args[1] = f"{args[1]},secure=on"
    command = [config.QEMU, *args]
    if kernel is not None:
        command += ["-kernel", str(kernel)]
    if bios is not None:
        command += ["-bios", str(bios)]
    return command


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


def print_failure_tail(capture: OutputCapture) -> None:
    if capture.stream is None and capture.tail:
        print("[nova demo] --- QEMU output tail ---", file=sys.stderr)
        print(capture.tail, file=sys.stderr, end="" if capture.tail.endswith("\n") else "\n")


def preserve_failure_tail(capture: OutputCapture, path: Path | None) -> None:
    print_failure_tail(capture)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(capture.tail, encoding="utf-8")


@dataclass(frozen=True)
class PatternMatch:
    index: int
    pattern: str
    elapsed_seconds: float
    waited_seconds: float
    remaining_seconds: float


def _format_traceback(exc: BaseException) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


class FailureKind(StrEnum):
    """Verification failure taxonomy; the value is the diagnostics JSON kind."""
    TIMEOUT = "timeout"
    EOF = "eof"
    FATAL = "fatal"
    FORBIDDEN = "forbidden"
    EXCEPTION = "exception"
    INTERRUPTED = "interrupted"
    SPAWN = "spawn"


@dataclass(frozen=True)
class VerificationResult:
    failure: FailureKind | None = None
    pattern: str | None = None
    wait_seconds: float = 0.0
    elapsed_seconds: float = 0.0
    remaining_seconds: float = 0.0
    error: str = ""
    traceback_text: str = ""
    termination_succeeded: bool = True
    termination_error: str = ""
    matches: tuple[PatternMatch, ...] = ()

    @property
    def ok(self) -> bool:
        return self.failure is None and self.termination_succeeded

    @property
    def termination_attempted(self) -> bool:
        # Only a failed spawn leaves nothing to terminate.
        return self.failure != FailureKind.SPAWN


class VerificationInterrupted(BaseException):
    def __init__(self, result: VerificationResult, cause: KeyboardInterrupt):
        super().__init__(str(cause))
        self.result = result
        self.cause = cause
        self.capture: OutputCapture | None = None


def _require_pexpect():
    try:
        import pexpect

        return pexpect
    except ImportError:
        raise SystemExit("QEMU verification requires python3-pexpect or pexpect")


@dataclass(frozen=True)
class CommandVerification:
    result: VerificationResult
    capture: OutputCapture


def run_command(
    command: Sequence[str],
    expectations: list[dict],
    timeout: float,
    *,
    stream,
    clock: Callable[[], float] = time.monotonic,
    on_match: Callable[[PatternMatch], None] | None = None,
    fatal_patterns: tuple[str, ...] = (),
    forbidden_patterns: tuple[str, ...] = (),
) -> CommandVerification:
    pexpect = _require_pexpect()
    capture = OutputCapture(stream)
    process.log([str(argument) for argument in command])
    try:
        child = pexpect.spawn(
            command[0],
            list(command[1:]),
            timeout=timeout,
            encoding="utf-8",
        )
    except (Exception, SystemExit) as exc:
        return CommandVerification(
            VerificationResult(
                failure=FailureKind.SPAWN,
                error=f"{type(exc).__name__}: {exc}",
                traceback_text=_format_traceback(exc),
                termination_succeeded=False,
                termination_error="not attempted: process was not started",
            ),
            capture,
        )
    child.logfile_read = capture
    try:
        result = verify_child_output(
            child,
            expectations,
            timeout,
            clock=clock,
            timeout_error=pexpect.TIMEOUT,
            eof_error=pexpect.EOF,
            on_match=on_match,
            fatal_patterns=fatal_patterns,
            forbidden_patterns=forbidden_patterns,
        )
    except VerificationInterrupted as interrupted:
        interrupted.capture = capture
        raise
    return CommandVerification(result, capture)


def verify_child_output(
    child,
    expectations: list[dict],
    scenario_timeout: float,
    *,
    clock: Callable[[], float],
    timeout_error: type[BaseException],
    eof_error: type[BaseException],
    on_match: Callable[[PatternMatch], None] | None = None,
    fatal_patterns: tuple[str, ...] = (),
    forbidden_patterns: tuple[str, ...] = (),
) -> VerificationResult:
    """Verify one spawned process and terminate it on every exit path."""
    started_at = 0.0
    deadline = 0.0
    matches: list[PatternMatch] = []
    result = VerificationResult()
    interrupted: KeyboardInterrupt | None = None

    def safe_clock() -> float:
        # A clock that raises must not mask the failure being reported.
        try:
            return clock()
        except (Exception, SystemExit):
            return started_at

    def make_result(kind: FailureKind | None, at: float, **extra) -> VerificationResult:
        # started_at/deadline/matches are read at call time, so the timings
        # always reflect the scenario window as it stood when the outcome
        # was decided.
        return VerificationResult(
            failure=kind,
            elapsed_seconds=max(0.0, at - started_at),
            remaining_seconds=max(0.0, deadline - at),
            matches=tuple(matches),
            **extra,
        )

    try:
        started_at = clock()
        deadline = started_at + scenario_timeout

        for index, exp in enumerate(expectations, start=1):
            pattern = exp["pattern"]
            within = float(exp.get("within_seconds", scenario_timeout))
            wait_started = clock()
            remaining = max(0.0, deadline - wait_started)
            if remaining == 0.0:
                result = make_result(FailureKind.TIMEOUT, wait_started, pattern=pattern)
                break
            wait = min(within, remaining)

            try:
                monitored_patterns = (*forbidden_patterns, pattern, *fatal_patterns)
                patterns = list(monitored_patterns) if forbidden_patterns or fatal_patterns else pattern
                matched_index = child.expect(patterns, timeout=wait)
            except timeout_error:
                result = make_result(
                    FailureKind.TIMEOUT, clock(), pattern=pattern, wait_seconds=wait)
                break
            except eof_error:
                result = make_result(
                    FailureKind.EOF, clock(), pattern=pattern, wait_seconds=wait)
                break

            matched_at = clock()
            expected_index = len(forbidden_patterns)
            if forbidden_patterns and matched_index < expected_index:
                result = make_result(
                    FailureKind.FORBIDDEN,
                    matched_at,
                    pattern=forbidden_patterns[matched_index],
                    wait_seconds=max(0.0, matched_at - wait_started),
                    error=f"while waiting for /{pattern}/",
                )
                break
            if fatal_patterns and matched_index > expected_index:
                fatal_index = matched_index - expected_index - 1
                result = make_result(
                    FailureKind.FATAL,
                    matched_at,
                    pattern=fatal_patterns[fatal_index],
                    wait_seconds=max(0.0, matched_at - wait_started),
                    error=f"while waiting for /{pattern}/",
                )
                break
            if matched_at > wait_started + wait:
                result = make_result(
                    FailureKind.TIMEOUT, matched_at, pattern=pattern, wait_seconds=wait)
                break
            matched = PatternMatch(
                index=index,
                pattern=pattern,
                elapsed_seconds=max(0.0, matched_at - started_at),
                waited_seconds=max(0.0, matched_at - wait_started),
                remaining_seconds=max(0.0, deadline - matched_at),
            )
            matches.append(matched)
            if on_match is not None:
                on_match(matched)

            # Input is causally tied to the matching prompt. Never send it
            # before the corresponding output has been observed.
            send = exp.get("send")
            if send is not None:
                child.send(send)
        else:
            result = make_result(None, clock())
    except KeyboardInterrupt as exc:
        interrupted = exc
        result = make_result(
            FailureKind.INTERRUPTED,
            safe_clock(),
            error="KeyboardInterrupt",
            traceback_text=_format_traceback(exc),
        )
    except (Exception, SystemExit) as exc:
        result = make_result(
            FailureKind.EXCEPTION,
            safe_clock(),
            error=f"{type(exc).__name__}: {exc}",
            traceback_text=_format_traceback(exc),
        )
    finally:
        termination_succeeded = False
        termination_error = ""
        try:
            termination_succeeded = bool(child.terminate(force=True))
            if not termination_succeeded:
                termination_error = "terminate(force=True) returned false"
        except KeyboardInterrupt as exc:
            termination_error = "KeyboardInterrupt"
            if interrupted is None:
                interrupted = exc
        except (Exception, SystemExit) as exc:
            termination_error = f"{type(exc).__name__}: {exc}"

        if result.failure is None and termination_succeeded and forbidden_patterns:
            try:
                drain_index = child.expect([*forbidden_patterns, eof_error], timeout=1.0)
                if drain_index < len(forbidden_patterns):
                    result = make_result(
                        FailureKind.FORBIDDEN,
                        clock(),
                        pattern=forbidden_patterns[drain_index],
                        error="after all expected output matched",
                    )
            except eof_error:
                pass
            except KeyboardInterrupt as exc:
                if interrupted is None:
                    interrupted = exc
            except (Exception, SystemExit) as exc:
                result = make_result(
                    FailureKind.EXCEPTION,
                    safe_clock(),
                    error=f"post-termination output check failed: {type(exc).__name__}: {exc}",
                    traceback_text=_format_traceback(exc),
                )

    final_result = replace(
        result,
        termination_succeeded=termination_succeeded,
        termination_error=termination_error,
    )
    if interrupted is not None:
        raise VerificationInterrupted(final_result, interrupted)
    return final_result


@dataclass(frozen=True)
class RepeatAttempt:
    number: int
    status: str
    elapsed_seconds: float
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "pass"


def run_repeated_verification(
    runs: int,
    verify_once: Callable[[int], int],
    *,
    clock: Callable[[], float],
    on_attempt: Callable[[RepeatAttempt], None] | None = None,
) -> list[RepeatAttempt]:
    """Run every attempt so a soak reports a useful success rate."""
    if runs < 1:
        raise ValueError("runs must be positive")

    attempts = []
    for number in range(1, runs + 1):
        started_at = clock()
        error = ""
        try:
            return_code = verify_once(number)
        except (Exception, SystemExit) as exc:
            return_code = 1
            error = f"{type(exc).__name__}: {exc}"
        elapsed = max(0.0, clock() - started_at)
        attempt = RepeatAttempt(
            number=number,
            status="pass" if return_code == 0 else "fail",
            elapsed_seconds=elapsed,
            error=error,
        )
        attempts.append(attempt)
        if on_attempt is not None:
            on_attempt(attempt)
    return attempts
