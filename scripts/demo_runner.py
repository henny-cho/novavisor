#!/usr/bin/env python3
"""
NovaVisor demo harness.

Reads a demo's manifest.yml, builds the hypervisor and demo guest(s),
launches QEMU with the guest loaded via -device loader, and verifies
that expected output patterns appear within their per-pattern deadlines.

Exits 0 on PASS, non-zero on any failure. CI gates on this exit code.

Demos are addressed by the numeric ID shown in `list` (the NN_ prefix of
the demo directory, e.g. `2` or `02` for 02_timer); the full directory
name is also accepted for scripts that already store it.

Usage:
    demo_runner.py list
    demo_runner.py fetch <id|name>      # populate the external image cache
    demo_runner.py run <id|name>        # launch without pattern checking
    demo_runner.py verify <id|name>     # launch and check manifest.expect
    demo_runner.py verify-repeat <id|name> --runs N
    demo_runner.py verify-all           # run all enabled demos sequentially
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


def _require_yaml():
    # Pure verifier tests do not parse manifests. Load this optional runtime
    # dependency only for commands that need it.
    try:
        import yaml
        return yaml
    except ImportError:
        sys.exit("demo_runner: missing PyYAML. Install with: apt-get install python3-yaml "
                 "or pip install --user PyYAML")


def _require_pexpect():
    # Keep discovery usable on minimal systems without process-control deps.
    try:
        import pexpect  # noqa: F401
        return pexpect
    except ImportError:
        sys.exit("demo_runner: missing pexpect. Install with: "
                 "apt-get install python3-pexpect or pip install --user pexpect")


REPO = Path(__file__).resolve().parent.parent
DEMO_DIR = REPO / "demo"
BUILD_DIR = REPO / "build"
DEMO_BUILD_DIR = BUILD_DIR / "demo"
HV_PRESET = "aarch64-debug"
HV_ELF = BUILD_DIR / HV_PRESET / "novavisor.elf"
QEMU = "qemu-system-aarch64"
# NOVA_GUEST_IPA_BASE (nova/abi/guest_layout.h): every guest links here.
GUEST_LINK_BASE = 0x50000000


@dataclass(frozen=True)
class RestoreMetric:
    vm: int
    written_bytes: int
    examined_bytes: int
    elapsed_ms: int


RESTORE_METRIC_PATTERN = re.compile(
    r"\[core_vcpu\] VM (?P<vm>\d+) restored "
    r"(?P<written>\d+)/(?P<examined>\d+) bytes in (?P<elapsed>\d+) ms"
)


class OutputCapture:
    """Keep a bounded diagnostic tail and stream only outside CI."""

    def __init__(self, stream, max_bytes: int = 32 * 1024):
        self.stream = stream
        self.max_bytes = max_bytes
        self.tail = ""
        self.restore_metrics: list[RestoreMetric] = []
        self._line_buffer = ""

    def write(self, data: str) -> None:
        if self.stream is not None:
            self.stream.write(data)
        self._consume_metrics(data)
        encoded = (self.tail + data).encode("utf-8")
        if len(encoded) > self.max_bytes:
            encoded = encoded[-self.max_bytes:]
        # The byte window may begin in the middle of a UTF-8 sequence.
        self.tail = encoded.decode("utf-8", errors="ignore")

    def flush(self) -> None:
        if self.stream is not None:
            self.stream.flush()

    def _consume_metrics(self, data: str) -> None:
        lines = (self._line_buffer + data).splitlines(keepends=True)
        self._line_buffer = ""
        for line in lines:
            if line.endswith(("\n", "\r")):
                self._parse_metric(line)
            else:
                self._line_buffer = line[-512:]

    def _parse_metric(self, line: str) -> None:
        match = RESTORE_METRIC_PATTERN.search(line)
        if match is None:
            return
        self.restore_metrics.append(RestoreMetric(
            vm=int(match.group("vm")),
            written_bytes=int(match.group("written")),
            examined_bytes=int(match.group("examined")),
            elapsed_ms=int(match.group("elapsed")),
        ))
        self.restore_metrics = self.restore_metrics[-32:]

    def finish_metrics(self) -> None:
        if self._line_buffer:
            self._parse_metric(self._line_buffer)
            self._line_buffer = ""


def print_failure_tail(capture: OutputCapture) -> None:
    if capture.stream is None and capture.tail:
        print("[demo_runner] --- QEMU output tail ---", file=sys.stderr)
        print(capture.tail, file=sys.stderr, end="" if capture.tail.endswith("\n") else "\n")


def preserve_failure_tail(capture: OutputCapture, path: Path | None) -> None:
    capture.finish_metrics()
    print_failure_tail(capture)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(capture.tail, encoding="utf-8")


# ---------------------------------------------------------------------------
# Manifest discovery
# ---------------------------------------------------------------------------

def load_manifest(name: str) -> tuple[Path, dict]:
    yaml = _require_yaml()
    manifest_path = DEMO_DIR / name / "manifest.yml"
    if not manifest_path.exists():
        sys.exit(f"demo_runner: no manifest at {manifest_path}")
    with open(manifest_path) as f:
        data = yaml.safe_load(f)
    return manifest_path, data


def iter_demos() -> list[tuple[str, dict]]:
    yaml = _require_yaml()
    out = []
    for p in sorted(DEMO_DIR.iterdir()):
        mf = p / "manifest.yml"
        if p.is_dir() and mf.exists():
            with open(mf) as f:
                out.append((p.name, yaml.safe_load(f)))
    return out


def demo_id(name: str) -> str:
    # The demo's ID is its directory's numeric NN_ prefix ("02_timer" → "02").
    prefix = name.split("_", 1)[0]
    return prefix if prefix.isdigit() else "-"


def resolve_demo(token: str) -> str:
    """Map a numeric ID ("2", "02") or a full directory name to the demo name."""
    names = [n for n, _ in iter_demos()]
    if token in names:
        return token
    if token.isdigit():
        matches = [n for n in names if demo_id(n) != "-" and int(demo_id(n)) == int(token)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            sys.exit(f"demo_runner: ID '{token}' is ambiguous: {', '.join(matches)}")
    available = ", ".join(f"{demo_id(n)}={n}" for n in names) or "(none)"
    sys.exit(f"demo_runner: unknown demo '{token}'. Available: {available}")


# ---------------------------------------------------------------------------
# Build steps
# ---------------------------------------------------------------------------

def run(cmd: list[str], **kw) -> None:
    print(f"[demo_runner] $ {' '.join(shlex.quote(c) for c in cmd)}")
    subprocess.check_call(cmd, **kw)


def build_hypervisor(config: str | None = None) -> Path:
    # Always delegate to task.sh: a no-change Ninja rebuild is nearly free,
    # while skipping on HV_ELF existence would verify against a stale binary
    # after source edits. `config` (repo-relative guest config YAML) flows
    # through --config; omitting it restores configs/default.yml, so one
    # demo's config never leaks into the next.
    cmd = [str(REPO / "scripts" / "task.sh"), "build"]
    if config is not None:
        cfg = REPO / config
        if not cfg.exists():
            sys.exit(f"demo_runner: guest config not found: {cfg}")
        cmd += ["--config", str(cfg)]
    run(cmd)
    return HV_ELF


def manifest_config(manifest: dict) -> str | None:
    # For run/debug (no variant loop): the top-level config, or the
    # first variant's — matching what verify() exercises first.
    variants = manifest.get("variants")
    if variants:
        return variants[0].get("config", manifest.get("config"))
    return manifest.get("config")


def build_demos() -> Path:
    # Configure once; rebuild is cheap.
    if not (DEMO_BUILD_DIR / "build.ninja").exists():
        DEMO_BUILD_DIR.mkdir(parents=True, exist_ok=True)
        run([
            "cmake", "-S", str(DEMO_DIR), "-B", str(DEMO_BUILD_DIR),
            "-G", "Ninja",
            "-DCMAKE_C_COMPILER=aarch64-none-elf-gcc",
            "-DCMAKE_ASM_COMPILER=aarch64-none-elf-gcc",
            "-DCMAKE_SYSTEM_NAME=Generic",
            "-DCMAKE_SYSTEM_PROCESSOR=aarch64",
            "-DCMAKE_TRY_COMPILE_TARGET_TYPE=STATIC_LIBRARY",
        ])
    run(["cmake", "--build", str(DEMO_BUILD_DIR)])
    return DEMO_BUILD_DIR


# ---------------------------------------------------------------------------
# QEMU command construction
# ---------------------------------------------------------------------------

def resolve_guest_binary(demo_name: str, demo_build: Path, manifest: dict, spec: dict) -> Path:
    # Search order: custom-built demo artifacts, then external cache for
    # prebuilt/reference images (Zephyr, Linux).
    candidates = [
        demo_build / demo_name / spec["binary"],
        REPO / "external" / "cache" / "guests" / demo_name / spec["binary"],
    ]
    for c in candidates:
        if c.exists():
            return c
    # External images are fetched explicitly, never as a run side effect.
    hint = (f"\nRun: scripts/task.sh demo fetch {demo_id(demo_name)}"
            if (DEMO_DIR / demo_name / "fetch.sh").exists() else "")
    sys.exit(f"demo_runner: guest binary not found for '{demo_name}': "
             f"tried {', '.join(str(c) for c in candidates)}{hint}")


def build_qemu_cmd(elf: Path, demo_name: str, demo_build: Path, manifest: dict) -> list[str]:
    cmd = [
        QEMU,
        "-machine", "virt,virtualization=on,gic-version=3",
        "-cpu", "cortex-a57",
        "-smp", "2",  # must match NOVA_BOARD_SMP_CPUS (board_layout.h)
        "-nographic",
        "-m", "1024",
        "-kernel", str(elf),
    ]
    for guest in manifest.get("guests", []):
        binary = resolve_guest_binary(demo_name, demo_build, manifest, guest)
        addr = guest["load_addr"]
        vcpus = guest.get("vcpus", 1)
        if not 1 <= vcpus <= 2:  # kMaxVcpusPerVm (nova/abi/guest.hpp)
            raise SystemExit(f"[demo_runner] {demo_name}: guest '{guest.get('name')}' asks for "
                             f"{vcpus} vcpus (supported: 1..2)")
        uart = guest.get("uart", "none")
        if uart not in ("none", "vuart"):  # UartKind (nova/abi/guest.hpp)
            raise SystemExit(f"[demo_runner] {demo_name}: guest '{guest.get('name')}' asks for "
                             f"uart '{uart}' (supported: none, vuart)")
        cmd += ["-device", f"loader,file={binary},addr={addr:#x},force-raw=on"]
    return cmd


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PatternMatch:
    index: int
    pattern: str
    elapsed_seconds: float
    waited_seconds: float
    remaining_seconds: float


@dataclass(frozen=True)
class VerificationResult:
    failure: str | None = None
    pattern: str | None = None
    wait_seconds: float = 0.0
    elapsed_seconds: float = 0.0
    remaining_seconds: float = 0.0
    error: str = ""
    traceback_text: str = ""
    termination_attempted: bool = True
    termination_succeeded: bool = True
    termination_error: str = ""
    matches: tuple[PatternMatch, ...] = ()

    @property
    def ok(self) -> bool:
        return self.failure is None and self.termination_succeeded


class VerificationInterrupted(BaseException):
    def __init__(self, result: VerificationResult, cause: KeyboardInterrupt):
        super().__init__(str(cause))
        self.result = result
        self.cause = cause


@dataclass(frozen=True)
class RepeatAttempt:
    number: int
    status: str
    elapsed_seconds: float
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "pass"


@dataclass(frozen=True)
class PreparedVerification:
    label: str
    phase: object
    command: tuple[str, ...]
    timeout_seconds: int
    expectations: tuple[dict, ...]


def diagnostics_path_for_tail(tail_path: Path) -> Path:
    suffix = ".qemu-tail.log"
    name = tail_path.name
    if name.endswith(suffix):
        name = name[:-len(suffix)]
    return tail_path.with_name(f"{name}.diagnostics.json")


def initialize_failure_artifacts(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for pattern in ("*.qemu-tail.log", "*.diagnostics.json"):
        for stale in path.glob(pattern):
            if stale.is_file():
                stale.unlink()


def preserve_failure_diagnostics(
    capture: OutputCapture,
    tail_path: Path | None,
    prepared: PreparedVerification,
    result: VerificationResult,
) -> None:
    preserve_failure_tail(capture, tail_path)
    if tail_path is None:
        return

    diagnostics = {
        "label": prepared.label,
        "failure": {
            "kind": result.failure,
            "pattern": result.pattern,
            "wait_seconds": result.wait_seconds,
            "elapsed_seconds": result.elapsed_seconds,
            "remaining_seconds": result.remaining_seconds,
            "error": result.error,
            "traceback": result.traceback_text,
        },
        "termination": {
            "attempted": result.termination_attempted,
            "succeeded": result.termination_succeeded,
            "error": result.termination_error,
        },
        "matches": [
            {
                "index": match.index,
                "pattern": match.pattern,
                "elapsed_seconds": match.elapsed_seconds,
                "waited_seconds": match.waited_seconds,
                "remaining_seconds": match.remaining_seconds,
            }
            for match in result.matches
        ],
        "restore_metrics": [
            {
                "vm": metric.vm,
                "written_bytes": metric.written_bytes,
                "examined_bytes": metric.examined_bytes,
                "elapsed_ms": metric.elapsed_ms,
            }
            for metric in capture.restore_metrics
        ],
    }
    path = diagnostics_path_for_tail(tail_path)
    path.write_text(f"{json.dumps(diagnostics, indent=2)}\n", encoding="utf-8")


def verify_child_output(
    child,
    expectations: list[dict],
    scenario_timeout: float,
    *,
    clock: Callable[[], float],
    timeout_error: type[BaseException],
    eof_error: type[BaseException],
    on_match: Callable[[PatternMatch], None] | None = None,
) -> VerificationResult:
    """Verify one spawned process and terminate it on every exit path."""
    started_at = 0.0
    deadline = 0.0
    matches: list[PatternMatch] = []
    result = VerificationResult()
    interrupted: KeyboardInterrupt | None = None

    try:
        started_at = clock()
        deadline = started_at + scenario_timeout

        for index, exp in enumerate(expectations, start=1):
            pattern = exp["pattern"]
            within = float(exp.get("within_seconds", scenario_timeout))
            wait_started = clock()
            remaining = max(0.0, deadline - wait_started)
            if remaining == 0.0:
                result = VerificationResult(
                    failure="timeout",
                    pattern=pattern,
                    elapsed_seconds=max(0.0, wait_started - started_at),
                    matches=tuple(matches),
                )
                break
            wait = min(within, remaining)

            try:
                child.expect(pattern, timeout=wait)
            except timeout_error:
                failed_at = clock()
                result = VerificationResult(
                    failure="timeout",
                    pattern=pattern,
                    wait_seconds=wait,
                    elapsed_seconds=max(0.0, failed_at - started_at),
                    remaining_seconds=max(0.0, deadline - failed_at),
                    matches=tuple(matches),
                )
                break
            except eof_error:
                failed_at = clock()
                result = VerificationResult(
                    failure="eof",
                    pattern=pattern,
                    wait_seconds=wait,
                    elapsed_seconds=max(0.0, failed_at - started_at),
                    remaining_seconds=max(0.0, deadline - failed_at),
                    matches=tuple(matches),
                )
                break

            matched_at = clock()
            if matched_at > wait_started + wait:
                result = VerificationResult(
                    failure="timeout",
                    pattern=pattern,
                    wait_seconds=wait,
                    elapsed_seconds=max(0.0, matched_at - started_at),
                    remaining_seconds=max(0.0, deadline - matched_at),
                    matches=tuple(matches),
                )
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
            finished_at = clock()
            result = VerificationResult(
                elapsed_seconds=max(0.0, finished_at - started_at),
                remaining_seconds=max(0.0, deadline - finished_at),
                matches=tuple(matches),
            )
    except KeyboardInterrupt as exc:
        interrupted = exc
        try:
            failed_at = clock()
        except (Exception, SystemExit):
            failed_at = started_at
        result = VerificationResult(
            failure="interrupted",
            elapsed_seconds=max(0.0, failed_at - started_at),
            remaining_seconds=max(0.0, deadline - failed_at),
            error="KeyboardInterrupt",
            traceback_text="".join(traceback.format_exception(
                type(exc),
                exc,
                exc.__traceback__,
            )),
            matches=tuple(matches),
        )
    except (Exception, SystemExit) as exc:
        try:
            failed_at = clock()
        except (Exception, SystemExit):
            failed_at = started_at
        result = VerificationResult(
            failure="exception",
            elapsed_seconds=max(0.0, failed_at - started_at),
            remaining_seconds=max(0.0, deadline - failed_at),
            error=f"{type(exc).__name__}: {exc}",
            traceback_text="".join(traceback.format_exception(
                type(exc),
                exc,
                exc.__traceback__,
            )),
            matches=tuple(matches),
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

    final_result = VerificationResult(
        failure=result.failure,
        pattern=result.pattern,
        wait_seconds=result.wait_seconds,
        elapsed_seconds=result.elapsed_seconds,
        remaining_seconds=result.remaining_seconds,
        error=result.error,
        traceback_text=result.traceback_text,
        termination_attempted=True,
        termination_succeeded=termination_succeeded,
        termination_error=termination_error,
        matches=result.matches,
    )
    if interrupted is not None:
        raise VerificationInterrupted(final_result, interrupted)
    return final_result


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


def initialize_repeat_summary(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as summary:
        csv.writer(summary).writerow(("run", "status", "elapsed_seconds", "error"))


def append_repeat_summary(path: Path, attempt: RepeatAttempt) -> None:
    with path.open("a", newline="") as summary:
        csv.writer(summary).writerow((
            attempt.number,
            attempt.status,
            f"{attempt.elapsed_seconds:.3f}",
            attempt.error,
        ))


def manifest_variants(manifest: dict) -> list[dict]:
    variants = manifest.get("variants")
    if variants is not None:
        return variants
    return [{
        "config": manifest.get("config"),
        "expect": manifest.get("expect", []),
    }]


def prepare_verification(
    name: str,
    manifest: dict,
    variant: dict,
    *,
    demo_build: Path | None = None,
    elf_snapshot: Path | None = None,
) -> PreparedVerification:
    label = name if "name" not in variant else f"{name}[{variant['name']}]"
    elf = build_hypervisor(variant.get("config"))
    if elf_snapshot is not None:
        elf_snapshot.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(elf, elf_snapshot)
        elf = elf_snapshot
    if demo_build is None:
        demo_build = build_demos()
    command = build_qemu_cmd(elf, name, demo_build, manifest)
    return PreparedVerification(
        label=label,
        phase=manifest.get("phase"),
        command=tuple(command),
        timeout_seconds=int(manifest.get("timeout_seconds", 30)),
        expectations=tuple(variant.get("expect", [])),
    )


def report_verification_failure(result: VerificationResult) -> None:
    if result.failure == "timeout":
        print(f"\n[demo_runner] FAIL: timeout waiting for /{result.pattern}/ "
              f"(wait limit {result.wait_seconds:.1f}s, elapsed {result.elapsed_seconds:.1f}s, "
              f"remaining {result.remaining_seconds:.1f}s)", file=sys.stderr)
    elif result.failure == "eof":
        print(f"\n[demo_runner] FAIL: EOF before /{result.pattern}/ "
              f"(elapsed {result.elapsed_seconds:.1f}s, "
              f"remaining {result.remaining_seconds:.1f}s)", file=sys.stderr)
    elif result.failure in ("exception", "interrupted"):
        print(f"\n[demo_runner] FAIL: verifier exception: {result.error} "
              f"(elapsed {result.elapsed_seconds:.1f}s, "
              f"remaining {result.remaining_seconds:.1f}s)", file=sys.stderr)

    if result.termination_attempted and not result.termination_succeeded:
        print(f"\n[demo_runner] FAIL: QEMU cleanup: {result.termination_error}",
              file=sys.stderr)


def run_prepared_verification(
    prepared: PreparedVerification,
    failure_tail: Path | None = None,
) -> int:
    pexpect = _require_pexpect()
    timeout = prepared.timeout_seconds
    print(f"[demo_runner] --- {prepared.label} (phase {prepared.phase}) timeout={timeout}s ---")
    print(f"[demo_runner] $ {' '.join(shlex.quote(c) for c in prepared.command)}")

    capture = OutputCapture(None if os.environ.get("GITHUB_ACTIONS") == "true" else sys.stdout)
    try:
        child = pexpect.spawn(
            prepared.command[0],
            list(prepared.command[1:]),
            timeout=timeout,
            encoding="utf-8",
        )
    except (Exception, SystemExit) as exc:
        result = VerificationResult(
            failure="spawn",
            error=f"{type(exc).__name__}: {exc}",
            traceback_text="".join(traceback.format_exception(
                type(exc),
                exc,
                exc.__traceback__,
            )),
            termination_attempted=False,
            termination_succeeded=False,
            termination_error="not attempted: process was not started",
        )
        print(f"\n[demo_runner] FAIL: QEMU spawn: {result.error}", file=sys.stderr)
        preserve_failure_diagnostics(capture, failure_tail, prepared, result)
        return 1
    child.logfile_read = capture

    def report_match(match: PatternMatch) -> None:
        print(f"[demo_runner] matched[{match.index}/{len(prepared.expectations)}] "
              f"/{match.pattern}/ elapsed={match.elapsed_seconds:.1f}s "
              f"wait={match.waited_seconds:.1f}s remaining={match.remaining_seconds:.1f}s")

    try:
        result = verify_child_output(
            child,
            list(prepared.expectations),
            timeout,
            clock=time.monotonic,
            timeout_error=pexpect.TIMEOUT,
            eof_error=pexpect.EOF,
            on_match=report_match,
        )
    except VerificationInterrupted as interrupted:
        report_verification_failure(interrupted.result)
        preserve_failure_diagnostics(capture, failure_tail, prepared, interrupted.result)
        raise interrupted.cause.with_traceback(interrupted.cause.__traceback__)
    except BaseException:
        preserve_failure_tail(capture, failure_tail)
        raise

    if not result.ok:
        report_verification_failure(result)
        preserve_failure_diagnostics(capture, failure_tail, prepared, result)
        return 1

    print(f"\n[demo_runner] PASS: {prepared.label}")
    return 0


def verify(name: str, artifact_dir: Path | None = None) -> int:
    _, manifest = load_manifest(name)
    if not manifest.get("enabled", False):
        print(f"[demo_runner] SKIP {name} (manifest.enabled=false)")
        return 0

    # A manifest is either a single run (top-level config/expect) or a
    # `variants:` list — one full run (build + QEMU + expect) each, with
    # the shared guests list. demo/11_configurable uses this to verify
    # the same guest under two configs.
    for index, variant in enumerate(manifest_variants(manifest), start=1):
        failure_tail = None
        if artifact_dir is not None:
            failure_tail = artifact_dir / f"{name}-variant-{index:02d}.qemu-tail.log"
        rc = _verify_one(name, manifest, variant, failure_tail)
        if rc != 0:
            return rc
    return 0


def _verify_one(
    name: str,
    manifest: dict,
    variant: dict,
    failure_tail: Path | None = None,
) -> int:
    return run_prepared_verification(
        prepare_verification(name, manifest, variant),
        failure_tail,
    )


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_fetch(args) -> int:
    # Delegate to the demo's own fetch.sh (pinned versions, idempotent
    # caching into external/cache/guests/<demo>/ live there).
    script = DEMO_DIR / args.name / "fetch.sh"
    if not script.exists():
        sys.exit(f"demo_runner: '{args.name}' has no fetch.sh (in-tree guests build via cmake)")
    return subprocess.call(["bash", str(script)])


def cmd_list(_args) -> int:
    demos = iter_demos()
    if not demos:
        print("(no demos)")
        return 0
    print(f"{'ID':>2}  {'NAME':30s}  {'PHASE':>5}  {'ENABLED':>7}  DESCRIPTION")
    for name, mf in demos:
        print(f"{demo_id(name):>2}  {name:30s}  {mf.get('phase', '?'):>5}  "
              f"{str(mf.get('enabled', False)).lower():>7}  "
              f"{mf.get('description', '')}")
    return 0


def cmd_run(args) -> int:
    # Non-verifying interactive launch. Useful for manual poking.
    _, manifest = load_manifest(args.name)
    elf = build_hypervisor(manifest_config(manifest))
    demo_build = build_demos()
    cmd = build_qemu_cmd(elf, args.name, demo_build, manifest)
    print(f"[demo_runner] $ {' '.join(shlex.quote(c) for c in cmd)}")
    print("[demo_runner] Press Ctrl-A x to exit QEMU.")
    return subprocess.call(cmd)


def cmd_debug(args) -> int:
    # Same as `run` but freezes QEMU at reset and opens a GDB stub on :1234.
    # Writes a gdb script with `add-symbol-file` lines for the selected demo's
    # guests so VS Code's launch config can `source` it and resolve guest
    # breakpoints alongside hypervisor ones. The ==> markers mirror
    # scripts/task.sh debug so .vscode/tasks.json's background problem matcher
    # works unchanged.
    _, manifest = load_manifest(args.name)
    elf = build_hypervisor(manifest_config(manifest))
    demo_build = build_demos()

    # Shared fixed path so .vscode/launch.json can `source` it without
    # substituting the demo name — keeps the launch config free of
    # ${input:...} which would otherwise prompt twice (once for the
    # task, once for setupCommands). Overwritten per debug session.
    symbols_script = demo_build / "debug-symbols.gdb"
    symbols_script.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# Auto-generated by demo_runner.py debug {args.name}"]
    for guest in manifest.get("guests", []):
        # Guest ELF sits next to its .bin with the extension stripped
        # (add_demo_guest(<name> ...) produces <name> and <name>.bin);
        # external images cache theirs as <stem>.elf next to the .bin.
        stem = Path(guest["binary"]).stem
        guest_elf = demo_build / args.name / stem
        if not guest_elf.exists():
            guest_elf = REPO / "external" / "cache" / "guests" / args.name / f"{stem}.elf"
        if guest_elf.exists():
            # Every guest links at the shared IPA window base but is
            # loaded at its PA slot — shift the symbols by the slot
            # offset so multi-VM demos resolve at the right addresses.
            offset = guest["load_addr"] - GUEST_LINK_BASE
            opt = f" -o {offset:#x}" if offset else ""
            lines.append(f"add-symbol-file {guest_elf}{opt}")
    symbols_script.write_text("\n".join(lines) + "\n")

    cmd = build_qemu_cmd(elf, args.name, demo_build, manifest) + ["-s", "-S"]
    print("==> Launching QEMU with GDB stub on :1234 (CPU halted).")
    print(f"==> Demo: {args.name}  guest symbols: {symbols_script}")
    print("==> Press Ctrl-A then x in QEMU to exit.")
    return subprocess.call(cmd)


def cmd_verify(args) -> int:
    artifact_dir = Path(args.artifacts) if args.artifacts else None
    if artifact_dir is not None:
        initialize_failure_artifacts(artifact_dir)
    return verify(args.name, artifact_dir)


def cmd_verify_repeat(args) -> int:
    _, manifest = load_manifest(args.name)
    if not manifest.get("enabled", False):
        print(f"[demo_runner] SKIP {args.name} (manifest.enabled=false)")
        return 0

    summary_path = Path(args.summary) if args.summary else None
    if summary_path is not None:
        initialize_repeat_summary(summary_path)

    artifact_dir = Path(args.artifacts) if args.artifacts else None
    if artifact_dir is not None:
        initialize_failure_artifacts(artifact_dir)

    demo_build = build_demos()
    prepared_runs = []
    for index, variant in enumerate(manifest_variants(manifest), start=1):
        snapshot = BUILD_DIR / "demo-repeat" / args.name / f"variant-{index}" / "novavisor.elf"
        prepared_runs.append(prepare_verification(
            args.name,
            manifest,
            variant,
            demo_build=demo_build,
            elf_snapshot=snapshot,
        ))

    def report_attempt(attempt: RepeatAttempt) -> None:
        print(f"[demo_runner] repeat {attempt.number}/{args.runs}: "
              f"{attempt.status.upper()} ({attempt.elapsed_seconds:.1f}s)")
        if attempt.error:
            print(f"[demo_runner] repeat error: {attempt.error}", file=sys.stderr)
        if summary_path is not None:
            append_repeat_summary(summary_path, attempt)

    def verify_once(attempt_number: int) -> int:
        for variant_number, prepared in enumerate(prepared_runs, start=1):
            failure_tail = None
            if artifact_dir is not None:
                failure_tail = (
                    artifact_dir
                    / f"attempt-{attempt_number:02d}-variant-{variant_number:02d}.qemu-tail.log"
                )
            return_code = run_prepared_verification(prepared, failure_tail)
            if return_code != 0:
                return return_code
        return 0

    attempts = run_repeated_verification(
        args.runs,
        verify_once,
        clock=time.monotonic,
        on_attempt=report_attempt,
    )
    passed = sum(attempt.ok for attempt in attempts)
    total_seconds = sum(attempt.elapsed_seconds for attempt in attempts)
    success_rate = 100.0 * passed / len(attempts)
    print(f"[demo_runner] repeat summary: {passed}/{len(attempts)} passed "
          f"({success_rate:.1f}%), total={total_seconds:.1f}s")
    return 0 if passed == len(attempts) else 1


def cmd_verify_all(args) -> int:
    demos = iter_demos()
    enabled = [(n, m) for n, m in demos if m.get("enabled", False)]
    if not enabled:
        print("[demo_runner] no enabled demos; nothing to verify.")
        return 0

    artifact_dir = Path(args.artifacts) if args.artifacts else None
    if artifact_dir is not None:
        initialize_failure_artifacts(artifact_dir)

    # Build once up front so per-demo failures don't keep rebuilding.
    build_hypervisor()
    build_demos()

    failures = []
    for name, _mf in enabled:
        rc = verify(name, artifact_dir)
        if rc != 0:
            failures.append(name)
    if failures:
        print(f"\n[demo_runner] {len(failures)} demo(s) failed: "
              f"{', '.join(failures)}", file=sys.stderr)
        return 1
    print(f"\n[demo_runner] all {len(enabled)} demo(s) passed.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="demo_runner")
    sub = p.add_subparsers(dest="subcommand", required=True)
    sub.add_parser("list", help="list demos and their enabled status")
    demo_arg = dict(metavar="id|name", help="demo ID from `list` (e.g. 2) or directory name (e.g. 02_timer)")
    p_fetch = sub.add_parser("fetch", help="populate the external image cache for a demo")
    p_fetch.add_argument("name", **demo_arg)
    p_run = sub.add_parser("run", help="launch a demo interactively")
    p_run.add_argument("name", **demo_arg)
    p_ver = sub.add_parser("verify", help="run a demo and check manifest.expect")
    p_ver.add_argument("name", **demo_arg)
    p_ver.add_argument("--artifacts", metavar="DIR",
                       help="write bounded diagnostics for a failed run")
    p_repeat = sub.add_parser("verify-repeat", help="repeat one demo and report its success rate")
    p_repeat.add_argument("name", **demo_arg)
    p_repeat.add_argument("--runs", type=int, required=True, choices=range(1, 101),
                          metavar="N", help="number of attempts (1..100)")
    p_repeat.add_argument("--summary", metavar="CSV",
                          help="write per-attempt status and elapsed time")
    p_repeat.add_argument("--artifacts", metavar="DIR",
                          help="write one bounded QEMU tail per failed attempt")
    p_all = sub.add_parser("verify-all", help="run all enabled demos")
    p_all.add_argument("--artifacts", metavar="DIR",
                       help="write bounded diagnostics for failed runs")
    p_dbg = sub.add_parser("debug", help="launch a demo with QEMU halted and GDB stub on :1234")
    p_dbg.add_argument("name", **demo_arg)
    args = p.parse_args()

    if hasattr(args, "name"):
        args.name = resolve_demo(args.name)

    dispatch = {
        "list": cmd_list,
        "fetch": cmd_fetch,
        "run": cmd_run,
        "verify": cmd_verify,
        "verify-repeat": cmd_verify_repeat,
        "verify-all": cmd_verify_all,
        "debug": cmd_debug,
    }
    return dispatch[args.subcommand](args)


if __name__ == "__main__":
    sys.exit(main())
