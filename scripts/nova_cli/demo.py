"""Subcommand implementations: the wiring between manifests, builds, and QEMU."""

from __future__ import annotations

import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import config, demo_build as build, manifest, process, report
from . import qemu


@dataclass(frozen=True)
class PreparedVerification:
    label: str
    phase: object
    command: tuple[str, ...]
    timeout_seconds: int
    expectations: tuple[dict, ...]
    forbidden_patterns: tuple[str, ...] = ()


def prepare_verification(
    name: str,
    demo_manifest: dict,
    variant: dict,
    *,
    demo_build: Path | None = None,
    elf_snapshot: Path | None = None,
) -> PreparedVerification:
    label = name if "name" not in variant else f"{name}[{variant['name']}]"
    forbidden_patterns = manifest.manifest_pattern_list(demo_manifest, "forbid")
    expectations = tuple(variant.get("expect", []))
    if forbidden_patterns and not expectations:
        raise SystemExit("[nova demo] manifest 'forbid' requires expected patterns")
    if demo_build is None:
        demo_build = build.build_demos()
    payloads = build.prepare_payload_manifest(name, demo_build, demo_manifest)
    elf = build.build_hypervisor(variant.get("config"), payloads)
    if elf_snapshot is not None:
        elf_snapshot.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(elf, elf_snapshot)
        elf = elf_snapshot
    command = build.build_qemu_cmd(elf, name, demo_build, demo_manifest)
    return PreparedVerification(
        label=label,
        phase=demo_manifest.get("phase"),
        command=tuple(command),
        timeout_seconds=int(demo_manifest.get("timeout_seconds", 30)),
        expectations=expectations,
        forbidden_patterns=forbidden_patterns,
    )


def run_prepared_verification(
    prepared: PreparedVerification,
    failure_tail: Path | None = None,
) -> int:
    timeout = prepared.timeout_seconds
    print(f"[nova demo] --- {prepared.label} (phase {prepared.phase}) timeout={timeout}s ---")

    def report_match(match: qemu.PatternMatch) -> None:
        print(f"[nova demo] matched[{match.index}/{len(prepared.expectations)}] "
              f"/{match.pattern}/ elapsed={match.elapsed_seconds:.1f}s "
              f"wait={match.waited_seconds:.1f}s remaining={match.remaining_seconds:.1f}s")

    try:
        verification = qemu.run_command(
            prepared.command,
            list(prepared.expectations),
            timeout,
            stream=None if os.environ.get("GITHUB_ACTIONS") == "true" else sys.stdout,
            clock=time.monotonic,
            on_match=report_match,
            fatal_patterns=config.FATAL_OUTPUT_PATTERNS,
            forbidden_patterns=prepared.forbidden_patterns,
        )
    except qemu.VerificationInterrupted as interrupted:
        report.report_verification_failure(interrupted.result)
        report.preserve_failure_diagnostics(
            interrupted.capture,
            failure_tail,
            prepared,
            interrupted.result,
        )
        raise interrupted.cause.with_traceback(
            interrupted.cause.__traceback__
        ) from None
    except BaseException:
        raise

    result = verification.result
    if not result.ok:
        report.report_verification_failure(result)
        report.preserve_failure_diagnostics(
            verification.capture,
            failure_tail,
            prepared,
            result,
        )
        return 1

    print(f"\n[nova demo] PASS: {prepared.label}")
    return 0


def verify(name: str, artifacts: report.ArtifactPaths | None = None) -> int:
    _, demo_manifest = manifest.load_manifest(name)
    if not demo_manifest.get("enabled", False):
        print(f"[nova demo] SKIP {name} (manifest.enabled=false)")
        return 0

    # A manifest is either a single run (top-level config/expect) or a
    # `variants:` list — one full run (build + QEMU + expect) each, with
    # the shared guests list. demo/11_configurable uses this to verify
    # the same guest under two configs.
    for index, variant in enumerate(manifest.manifest_variants(demo_manifest), start=1):
        failure_tail = None if artifacts is None else artifacts.verify_tail(name, index)
        rc = run_prepared_verification(
            prepare_verification(name, demo_manifest, variant),
            failure_tail,
        )
        if rc != 0:
            return rc
    return 0


def _prepare_launch(name: str) -> tuple[Path, dict, Path, list[str]]:
    """Shared run/debug prologue: build everything the demo needs and compose
    its QEMU command line."""
    _, demo_manifest = manifest.load_manifest(name)
    demo_build = build.build_demos()
    payloads = build.prepare_payload_manifest(name, demo_build, demo_manifest)
    elf = build.build_hypervisor(manifest.manifest_config(demo_manifest), payloads)
    cmd = build.build_qemu_cmd(elf, name, demo_build, demo_manifest)
    return demo_build, demo_manifest, elf, cmd


def cmd_build(_args) -> int:
    build.build_demos()
    return 0


def cmd_fetch(args) -> int:
    # Delegate to the demo's own fetch.sh (pinned versions, idempotent
    # caching into external/cache/guests/<demo>/ live there).
    if args.all:
        # Every enabled demo with an external image recipe — the single
        # source for what CI must fetch before verify-all.
        names = [name for name, mf in manifest.iter_demos()
                 if mf.get("enabled", False) and (config.DEMO_DIR / name / "fetch.sh").exists()]
        for name in names:
            rc = process.call(["bash", str(config.DEMO_DIR / name / "fetch.sh")])
            if rc != 0:
                return rc
        print(f"[nova demo] fetched {len(names)} demo image set(s).")
        return 0
    if args.name is None:
        sys.exit("nova demo: fetch requires a demo id/name or --all")
    script = config.DEMO_DIR / args.name / "fetch.sh"
    if not script.exists():
        sys.exit(f"nova demo: '{args.name}' has no fetch.sh; in-tree guests use build")
    return process.call(["bash", str(script)])


def cmd_qemu_args(_args) -> int:
    # Consumed by scripts/task.sh run/debug so the board model has one owner.
    print(" ".join(config.QEMU_BOARD_ARGS))
    return 0


def cmd_list(_args) -> int:
    demos = manifest.iter_demos()
    if not demos:
        print("(no demos)")
        return 0
    print(f"{'ID':>2}  {'NAME':30s}  {'PHASE':>5}  {'ENABLED':>7}  DESCRIPTION")
    for name, mf in demos:
        print(f"{manifest.demo_id(name):>2}  {name:30s}  {mf.get('phase', '?'):>5}  "
              f"{str(mf.get('enabled', False)).lower():>7}  "
              f"{mf.get('description', '')}")
    return 0


def cmd_run(args) -> int:
    # Non-verifying interactive launch. Useful for manual poking.
    _demo_build, _demo_manifest, _elf, cmd = _prepare_launch(args.name)
    print("[nova demo] Press Ctrl-A x to exit QEMU.")
    return process.call(cmd)


def cmd_debug(args) -> int:
    # Same as `run` but freezes QEMU at reset and opens a GDB stub on :1234.
    # Writes a gdb script with `add-symbol-file` lines for the selected demo's
    # guests so VS Code's launch config can `source` it and resolve guest
    # breakpoints alongside hypervisor ones. The ==> markers mirror
    # scripts/task.sh debug so .vscode/tasks.json's background problem matcher
    # works unchanged.
    demo_build, demo_manifest, _elf, cmd = _prepare_launch(args.name)

    # Shared fixed path so .vscode/launch.json can `source` it without
    # substituting the demo name — keeps the launch config free of
    # ${input:...} which would otherwise prompt twice (once for the
    # task, once for setupCommands). Overwritten per debug session.
    symbols_script = demo_build / "debug-symbols.gdb"
    symbols_script.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# Auto-generated by nova demo debug {args.name}"]
    for guest in demo_manifest.get("guests", []):
        # Guest ELF sits next to its .bin with the extension stripped
        # (add_demo_guest(<name> ...) produces <name> and <name>.bin);
        # external images cache theirs as <stem>.elf next to the .bin.
        stem = Path(guest["binary"]).stem
        guest_elf = demo_build / args.name / stem
        if not guest_elf.exists():
            guest_elf = config.REPO / "external" / "cache" / "guests" / args.name / f"{stem}.elf"
        if guest_elf.exists():
            # Every guest links at the shared IPA window base but is
            # loaded at its PA slot — shift the symbols by the slot
            # offset so multi-VM demos resolve at the right addresses.
            offset = guest["load_addr"] - config.GUEST_LINK_BASE
            opt = f" -o {offset:#x}" if offset else ""
            lines.append(f"add-symbol-file {guest_elf}{opt}")
    symbols_script.write_text("\n".join(lines) + "\n")

    print("==> Launching QEMU with GDB stub on :1234 (CPU halted).")
    print(f"==> Demo: {args.name}  guest symbols: {symbols_script}")
    print("==> Press Ctrl-A then x in QEMU to exit.")
    return process.call(cmd + ["-s", "-S"])


def cmd_verify(args) -> int:
    return verify(args.name, report.ArtifactPaths.from_arg(args.artifacts))


def cmd_verify_repeat(args) -> int:
    _, demo_manifest = manifest.load_manifest(args.name)
    if not demo_manifest.get("enabled", False):
        print(f"[nova demo] SKIP {args.name} (manifest.enabled=false)")
        return 0

    summary_path = Path(args.summary) if args.summary else None
    if summary_path is not None:
        report.initialize_repeat_summary(summary_path)

    artifacts = report.ArtifactPaths.from_arg(args.artifacts)

    demo_build = build.build_demos()
    prepared_runs = []
    elf_snapshots = []
    for index, variant in enumerate(manifest.manifest_variants(demo_manifest), start=1):
        snapshot = (config.BUILD_ROOT / "demo-repeat" / args.name
                    / f"variant-{index}" / "novavisor.elf")
        elf_snapshots.append(snapshot)
        prepared_runs.append(prepare_verification(
            args.name,
            demo_manifest,
            variant,
            demo_build=demo_build,
            elf_snapshot=snapshot,
        ))

    def report_attempt(attempt: qemu.RepeatAttempt) -> None:
        print(f"[nova demo] repeat {attempt.number}/{args.runs}: "
              f"{attempt.status.upper()} ({attempt.elapsed_seconds:.1f}s)")
        if attempt.error:
            print(f"[nova demo] repeat error: {attempt.error}", file=sys.stderr)
        if summary_path is not None:
            report.append_repeat_summary(summary_path, attempt)

    def verify_once(attempt_number: int) -> int:
        for variant_number, prepared in enumerate(prepared_runs, start=1):
            failure_tail = (
                None if artifacts is None
                else artifacts.repeat_tail(attempt_number, variant_number)
            )
            return_code = run_prepared_verification(prepared, failure_tail)
            if return_code != 0:
                return return_code
        return 0

    attempts = qemu.run_repeated_verification(
        args.runs,
        verify_once,
        clock=time.monotonic,
        on_attempt=report_attempt,
    )
    passed = sum(attempt.ok for attempt in attempts)
    total_seconds = sum(attempt.elapsed_seconds for attempt in attempts)
    success_rate = 100.0 * passed / len(attempts)
    print(f"[nova demo] repeat summary: {passed}/{len(attempts)} passed "
          f"({success_rate:.1f}%), total={total_seconds:.1f}s")
    report.append_github_summary(f"{args.name} recovery soak", attempts, summary_path)
    if passed != len(attempts) and artifacts is not None:
        report.collect_evidence(artifacts, args.name, demo_manifest, elf_snapshots)
    return 0 if passed == len(attempts) else 1


def cmd_verify_all(args) -> int:
    demos = manifest.iter_demos()
    enabled = [(n, m) for n, m in demos if m.get("enabled", False)]
    if not enabled:
        print("[nova demo] no enabled demos; nothing to verify.")
        return 0

    artifacts = report.ArtifactPaths.from_arg(args.artifacts)

    # Build once up front so per-demo failures don't keep rebuilding.
    build.build_hypervisor()
    build.build_demos()

    failures = []
    for name, _mf in enabled:
        rc = verify(name, artifacts)
        if rc != 0:
            failures.append(name)
    if failures:
        print(f"\n[nova demo] {len(failures)} demo(s) failed: "
              f"{', '.join(failures)}", file=sys.stderr)
        return 1
    print(f"\n[nova demo] all {len(enabled)} demo(s) passed.")
    return 0


def register(subcommands) -> None:
    parser = subcommands.add_parser("demo", help="build and verify demo guests")
    operations = parser.add_subparsers(dest="demo_command", required=True)

    operations.add_parser("list", help="list demos").set_defaults(handler=cmd_list)
    operations.add_parser("build", help="build in-tree guests").set_defaults(
        handler=cmd_build
    )
    operations.add_parser("qemu-args", help="print board flags").set_defaults(
        handler=cmd_qemu_args
    )

    demo_arg = {
        "metavar": "id|name",
        "type": manifest.resolve_demo,
        "help": "demo ID or directory name",
    }
    fetch = operations.add_parser("fetch", help="populate external image cache")
    fetch.add_argument("name", nargs="?", **demo_arg)
    fetch.add_argument("--all", action="store_true")
    fetch.set_defaults(handler=cmd_fetch)

    run = operations.add_parser("run", help="launch interactively")
    run.add_argument("name", **demo_arg)
    run.set_defaults(handler=cmd_run)

    verify = operations.add_parser("verify", help="check expected output")
    verify.add_argument("name", **demo_arg)
    verify.add_argument("--artifacts", metavar="DIR")
    verify.set_defaults(handler=cmd_verify)

    repeat = operations.add_parser("verify-repeat", help="repeat one verification")
    repeat.add_argument("name", **demo_arg)
    repeat.add_argument(
        "--runs",
        type=int,
        required=True,
        choices=range(1, 101),
        metavar="N",
    )
    repeat.add_argument("--summary", metavar="CSV")
    repeat.add_argument("--artifacts", metavar="DIR")
    repeat.set_defaults(handler=cmd_verify_repeat)

    verify_all = operations.add_parser("verify-all", help="verify enabled demos")
    verify_all.add_argument("--artifacts", metavar="DIR")
    verify_all.set_defaults(handler=cmd_verify_all)

    debug = operations.add_parser("debug", help="launch halted with a GDB stub")
    debug.add_argument("name", **demo_arg)
    debug.set_defaults(handler=cmd_debug)
