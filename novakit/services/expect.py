"""What a run must show, and the state machine that decides whether it did.

Nothing here spawns a process or names a file: it observes an already-running
child against a list of steps and reports the outcome. That keeps the
decision logic testable against a fake child, with no pexpect in sight.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Callable

# How often a handled step is asked whether it is satisfied. Short
# enough that the console keeps flowing between looks, long enough that
# a step waiting seconds does not spend the wait in syscalls.
POLL_SECONDS = 0.2


@dataclass(frozen=True)
class Scenario:
    """One verifiable run: what to launch and what it must do.

    `elf` is the image the command boots. Carried rather than read back
    out of the command line: whoever built the command already had it,
    and a reader deriving it again is a second answer to a question that
    was asked once.
    """
    label: str
    phase: object
    command: tuple[str, ...]
    timeout_seconds: int
    steps: tuple[dict, ...]
    forbidden_patterns: tuple[str, ...] = ()
    elf: object | None = None
    # A run whose subject is the panic path: the firmware's failure
    # report is what it came to show, so that one guard stands down.
    expects_panic: bool = False


# The manifest names a step by the key it carries, so a step is exactly
# as self-describing as its own entry. Only `pattern` is carried out
# here; the rest are handed to injected handlers, so this module never
# learns what a shared memory region or a command ring is.
#
# `command` and `walk` are both things the host does, and they are two
# words because they are two different acts: a command is executed by
# the machine and answered on the trace ring, where a walk is a question
# an observer asks and an observer answers. One word for both would
# leave a demo unable to say which of them a run did.
STEP_KINDS: tuple[str, ...] = ("pattern", "observe", "event", "command", "walk")


def step_kind(step: dict) -> str:
    for kind in STEP_KINDS:
        if kind in step:
            return kind
    raise KeyError(f"step names no kind: {sorted(step)} (want one of {list(STEP_KINDS)})")


def step_subject(step: dict) -> str:
    return str(step[step_kind(step)])


def describe_step(kind: str, subject: str) -> str:
    """One phrasing of a step, for every place that reports one."""
    return f"/{subject}/" if kind == "pattern" else f"{kind} {subject}"


def needs_observation(steps) -> bool:
    """Whether this run has to be observable, asked of the steps alone.

    Derived rather than declared: the manifest already says what it
    wants by the steps it lists, and a second field saying "and observe
    me" is the same fact twice.
    """
    return any(step_kind(step) != "pattern" for step in steps)


@dataclass(frozen=True)
class StepOutcome:
    """Where a handled step stands: still waiting, carried out, or failed.

    A pending outcome may carry a note — something the handler learned
    while waiting that does not settle the step but explains it if the
    wait runs out. A hole in a ring is the case this exists for: it does
    not mean the event never happened, and a timeout that did not
    mention it would report a defect the machine may not have.
    """
    done: bool = False
    error: str = ""
    note: str = ""


PENDING = StepOutcome()
CARRIED = StepOutcome(done=True)


def step_pending(note: str) -> StepOutcome:
    return StepOutcome(note=note)


def step_failed(reason: str) -> StepOutcome:
    return StepOutcome(done=True, error=reason)


# A handler turns one manifest entry into a poll. Polling rather than
# blocking is what lets this module keep the console drained and the
# forbidden-output guard live while a step that is not a pattern waits.
StepHandler = Callable[[dict], Callable[[], StepOutcome]]


@dataclass(frozen=True)
class StepResult:
    index: int
    kind: str
    subject: str
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
    # The step the run was on when it failed, and — for output that must
    # never appear — the pattern that hit instead. Two fields because
    # they answer different questions: what was owed, and what arrived.
    step_kind: str = ""
    step_subject: str = ""
    offender: str = ""
    wait_seconds: float = 0.0
    elapsed_seconds: float = 0.0
    remaining_seconds: float = 0.0
    error: str = ""
    traceback_text: str = ""
    termination_succeeded: bool = True
    termination_error: str = ""
    results: tuple[StepResult, ...] = ()

    @property
    def step(self) -> str:
        return describe_step(self.step_kind, self.step_subject)

    @property
    def ok(self) -> bool:
        return self.failure is None and self.termination_succeeded

    @property
    def termination_attempted(self) -> bool:
        # Only a failed spawn leaves nothing to terminate.
        return self.failure != FailureKind.SPAWN


class Interrupted(BaseException):
    """Ctrl-C during a run: carries the diagnostics, then lets the signal win."""

    def __init__(self, result: VerificationResult, cause: KeyboardInterrupt):
        super().__init__(str(cause))
        self.result = result
        self.cause = cause
        self.capture = None


def spawn_failure(exc: BaseException) -> VerificationResult:
    """The outcome when the process never started."""
    return VerificationResult(
        failure=FailureKind.SPAWN,
        error=f"{type(exc).__name__}: {exc}",
        traceback_text=_format_traceback(exc),
        termination_succeeded=False,
        termination_error="not attempted: process was not started",
    )


def observe_output(
    child,
    steps: list[dict],
    scenario_timeout: float,
    *,
    clock: Callable[[], float],
    timeout_error: type[BaseException],
    eof_error: type[BaseException],
    on_step: Callable[[StepResult], None] | None = None,
    fatal_patterns: tuple[str, ...] = (),
    forbidden_patterns: tuple[str, ...] = (),
    handlers: dict[str, StepHandler] | None = None,
    poll_seconds: float = POLL_SECONDS,
) -> VerificationResult:
    """Verify one spawned process and terminate it on every exit path."""
    handlers = handlers or {}
    started_at = 0.0
    deadline = 0.0
    results: list[StepResult] = []
    result = VerificationResult()
    interrupted: KeyboardInterrupt | None = None

    def safe_clock() -> float:
        # A clock that raises must not mask the failure being reported.
        try:
            return clock()
        except (Exception, SystemExit):
            return started_at

    def make_result(kind: FailureKind | None, at: float, **extra) -> VerificationResult:
        # started_at/deadline/results are read at call time, so the timings
        # always reflect the scenario window as it stood when the outcome
        # was decided.
        return VerificationResult(
            failure=kind,
            elapsed_seconds=max(0.0, at - started_at),
            remaining_seconds=max(0.0, deadline - at),
            results=tuple(results),
            **extra,
        )

    # Output that must never appear is watched the same way whatever a
    # step waits for: the bands sit at the front of every monitored list
    # and the awaited thing goes last. The drain after the last step
    # watches the same bands, so no step kind slips past the guard.
    banned = (*forbidden_patterns, *fatal_patterns)

    def banned_hit(at_index: int, at: float, wait_started: float, owed: dict):
        if at_index < len(forbidden_patterns):
            kind_, offender = FailureKind.FORBIDDEN, forbidden_patterns[at_index]
        elif at_index < len(banned):
            kind_ = FailureKind.FATAL
            offender = fatal_patterns[at_index - len(forbidden_patterns)]
        else:
            return None
        return make_result(
            kind_, at, offender=offender,
            wait_seconds=max(0.0, at - wait_started), **owed)

    try:
        started_at = clock()
        deadline = started_at + scenario_timeout

        def await_handled(handler, step, wait, wait_started, owed):
            """Poll a handled step, draining the console between looks.

            Blocking on the handler would leave the pty unread, and a
            talkative guest then stops on a write while this waits for
            it. Reading through `expect` on the banned bands keeps the
            forbidden-output guard live in the same breath.
            """
            poll = handler(step)
            watched = [*banned, eof_error]
            note = ""
            while True:
                outcome = poll()
                if outcome.done:
                    return (
                        make_result(FailureKind.EXCEPTION, clock(),
                                    error=outcome.error, wait_seconds=wait, **owed)
                        if outcome.error else None
                    )
                note = outcome.note or note
                now = clock()
                if now >= wait_started + wait:
                    return make_result(
                        FailureKind.TIMEOUT, now, wait_seconds=wait, error=note, **owed)
                slice_seconds = min(poll_seconds, wait_started + wait - now)
                try:
                    hit = child.expect(watched, timeout=slice_seconds)
                except timeout_error:
                    continue  # nothing arrived; the read drained the pty anyway
                except eof_error:
                    return make_result(
                        FailureKind.EOF, clock(), wait_seconds=wait, **owed)
                at = clock()
                violation = banned_hit(hit, at, wait_started, owed)
                if violation is not None:
                    return violation
                # The trailing slot is the EOF class: the child is gone.
                return make_result(FailureKind.EOF, at, wait_seconds=wait, **owed)

        for index, step in enumerate(steps, start=1):
            kind, subject = step_kind(step), step_subject(step)
            # Every outcome from here names the step it was on, so the
            # failure headline never has to be assembled from free text.
            owed = {"step_kind": kind, "step_subject": subject}
            within = float(step.get("within_seconds", scenario_timeout))
            wait_started = clock()
            remaining = max(0.0, deadline - wait_started)
            if remaining == 0.0:
                result = make_result(FailureKind.TIMEOUT, wait_started, **owed)
                break
            wait = min(within, remaining)

            if kind == "pattern":
                try:
                    patterns = [*banned, subject] if banned else subject
                    hit = child.expect(patterns, timeout=wait)
                except timeout_error:
                    result = make_result(
                        FailureKind.TIMEOUT, clock(), wait_seconds=wait, **owed)
                    break
                except eof_error:
                    result = make_result(
                        FailureKind.EOF, clock(), wait_seconds=wait, **owed)
                    break
                matched_at = clock()
                violation = banned_hit(hit, matched_at, wait_started, owed) if banned else None
                if violation is not None:
                    result = violation
                    break
                if matched_at > wait_started + wait:
                    result = make_result(
                        FailureKind.TIMEOUT, matched_at, wait_seconds=wait, **owed)
                    break
            else:
                handler = handlers.get(kind)
                if handler is None:
                    result = make_result(
                        FailureKind.EXCEPTION, clock(),
                        error=f"no handler for a {kind} step in this run", **owed)
                    break
                failure = await_handled(handler, step, wait, wait_started, owed)
                if failure is not None:
                    result = failure
                    break
                matched_at = clock()
            carried = StepResult(
                index=index,
                kind=kind,
                subject=subject,
                elapsed_seconds=max(0.0, matched_at - started_at),
                waited_seconds=max(0.0, matched_at - wait_started),
                remaining_seconds=max(0.0, deadline - matched_at),
            )
            results.append(carried)
            if on_step is not None:
                on_step(carried)

            # Input is causally tied to the matching prompt. Never send it
            # before the corresponding output has been observed.
            send = step.get("send")
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

        if result.failure is None and termination_succeeded and banned:
            try:
                drain_index = child.expect([*banned, eof_error], timeout=1.0)
                violation = banned_hit(
                    drain_index, clock(), started_at,
                    {"error": "after every step was carried out"})
                if violation is not None:
                    result = violation
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
        raise Interrupted(final_result, interrupted)
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


def run_repeated(
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
