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
    demo_runner.py verify-all           # run all enabled demos sequentially
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("demo_runner: missing PyYAML. Install with: apt-get install python3-yaml "
             "or pip install --user PyYAML")


def _require_pexpect():
    # pexpect is only needed for run/verify/verify-all. Keep `list` usable
    # on minimal systems.
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


# ---------------------------------------------------------------------------
# Manifest discovery
# ---------------------------------------------------------------------------

def load_manifest(name: str) -> tuple[Path, dict]:
    manifest_path = DEMO_DIR / name / "manifest.yml"
    if not manifest_path.exists():
        sys.exit(f"demo_runner: no manifest at {manifest_path}")
    with open(manifest_path) as f:
        data = yaml.safe_load(f)
    return manifest_path, data


def iter_demos() -> list[tuple[str, dict]]:
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

def verify(name: str) -> int:
    _, manifest = load_manifest(name)
    if not manifest.get("enabled", False):
        print(f"[demo_runner] SKIP {name} (manifest.enabled=false)")
        return 0

    # A manifest is either a single run (top-level config/expect) or a
    # `variants:` list — one full run (build + QEMU + expect) each, with
    # the shared guests list. demo/11_configurable uses this to verify
    # the same guest under two configs.
    variants = manifest.get("variants")
    if variants is None:
        variants = [{"config": manifest.get("config"),
                     "expect": manifest.get("expect", [])}]
    for variant in variants:
        rc = _verify_one(name, manifest, variant)
        if rc != 0:
            return rc
    return 0


def _verify_one(name: str, manifest: dict, variant: dict) -> int:
    pexpect = _require_pexpect()
    label = name if "name" not in variant else f"{name}[{variant['name']}]"
    elf = build_hypervisor(variant.get("config"))
    demo_build = build_demos()
    cmd = build_qemu_cmd(elf, name, demo_build, manifest)

    timeout = int(manifest.get("timeout_seconds", 30))
    print(f"[demo_runner] --- {label} (phase {manifest.get('phase')}) timeout={timeout}s ---")
    print(f"[demo_runner] $ {' '.join(shlex.quote(c) for c in cmd)}")

    child = pexpect.spawn(cmd[0], cmd[1:], timeout=timeout, encoding="utf-8")
    child.logfile_read = sys.stdout

    try:
        for exp in variant.get("expect", []):
            pattern = exp["pattern"]
            within = int(exp.get("within_seconds", timeout))
            try:
                child.expect(pattern, timeout=within)
            except pexpect.TIMEOUT:
                print(f"\n[demo_runner] FAIL: timeout waiting for /{pattern}/ "
                      f"({within}s)", file=sys.stderr)
                return 1
            except pexpect.EOF:
                print(f"\n[demo_runner] FAIL: EOF before /{pattern}/",
                      file=sys.stderr)
                return 1
            # Optional host input, sent only after the pattern above
            # matched — keeps stdin-driven demos deterministic.
            send = exp.get("send")
            if send is not None:
                child.send(send)
        print(f"\n[demo_runner] PASS: {label}")
        return 0
    finally:
        child.terminate(force=True)


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
    return verify(args.name)


def cmd_verify_all(_args) -> int:
    demos = iter_demos()
    enabled = [(n, m) for n, m in demos if m.get("enabled", False)]
    if not enabled:
        print("[demo_runner] no enabled demos; nothing to verify.")
        return 0

    # Build once up front so per-demo failures don't keep rebuilding.
    build_hypervisor()
    build_demos()

    failures = []
    for name, _mf in enabled:
        rc = verify(name)
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
    sub.add_parser("verify-all", help="run all enabled demos")
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
        "verify-all": cmd_verify_all,
        "debug": cmd_debug,
    }
    return dispatch[args.subcommand](args)


if __name__ == "__main__":
    sys.exit(main())
