"""Build steps and QEMU command construction.

Turns a manifest into the artifacts a run needs: the hypervisor ELF, the demo
guest binaries, an optional embedded-payload manifest, and the QEMU argv.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Callable

from . import settings
from .manifest import demo_id, payload_mode, validate

_make_record = None


def run(cmd: list[str], **kw) -> None:
    print(f"[demo_runner] $ {' '.join(shlex.quote(c) for c in cmd)}")
    subprocess.check_call(cmd, **kw)


def _ensure_build_env() -> None:
    # Make direct invocation work like the task.sh path: cross toolchain on
    # PATH and CPM checkouts routed to the shared project-local cache.
    toolchain_bin = settings.REPO / ".toolchain" / "current" / "bin"
    if toolchain_bin.is_dir() and str(toolchain_bin) not in os.environ["PATH"].split(os.pathsep):
        os.environ["PATH"] = f"{toolchain_bin}{os.pathsep}{os.environ['PATH']}"
    os.environ.setdefault("CPM_SOURCE_CACHE", str(settings.REPO / "external" / "cache"))


def _sync_active(src: Path, dest: Path) -> None:
    # Copy only on content change so Ninja regenerates the guest DTBs
    # exactly when the input differs — no CMake reconfigure involved.
    dest.parent.mkdir(parents=True, exist_ok=True)
    content = src.read_bytes()
    if not dest.exists() or dest.read_bytes() != content:
        dest.write_bytes(content)


def build_hypervisor(config: str | None = None, payloads: Path | None = None) -> Path:
    # A no-change Ninja rebuild is nearly free, while skipping on ELF
    # existence would verify against a stale binary after source edits.
    # Omitting config/payloads restores the defaults, so one demo's
    # choice never leaks into the next.
    cfg = settings.REPO / (config if config is not None else "configs/default.yml")
    if not cfg.exists():
        sys.exit(f"demo_runner: guest config not found: {cfg}")
    payload_manifest = (payloads if payloads is not None
                        else settings.REPO / "configs" / "payloads.yml")
    if not payload_manifest.exists():
        sys.exit(f"demo_runner: payload manifest not found: {payload_manifest}")

    _ensure_build_env()
    preset_dir = settings.BUILD_DIR / settings.HV_PRESET
    _sync_active(cfg, preset_dir / "active_config.yml")
    _sync_active(payload_manifest, preset_dir / "active_payloads.yml")
    # Configure only on the first run; afterwards Ninja re-runs CMake by
    # itself whenever a CMakeLists.txt changes.
    if not (preset_dir / "build.ninja").exists():
        run(["cmake", "--preset", settings.HV_PRESET], cwd=settings.REPO)
    run(["cmake", "--build", "--preset", settings.HV_PRESET], cwd=settings.REPO)
    return settings.hv_elf()


def build_demos() -> Path:
    # Configure once; rebuild is cheap.
    _ensure_build_env()
    demo_build = settings.DEMO_BUILD_DIR
    if not (demo_build / "build.ninja").exists():
        demo_build.mkdir(parents=True, exist_ok=True)
        run([
            "cmake", "-S", str(settings.DEMO_DIR), "-B", str(demo_build),
            "-G", "Ninja",
            "-DCMAKE_C_COMPILER=aarch64-none-elf-gcc",
            "-DCMAKE_ASM_COMPILER=aarch64-none-elf-gcc",
            "-DCMAKE_SYSTEM_NAME=Generic",
            "-DCMAKE_SYSTEM_PROCESSOR=aarch64",
            "-DCMAKE_TRY_COMPILE_TARGET_TYPE=STATIC_LIBRARY",
        ])
    run(["cmake", "--build", str(demo_build)])
    return demo_build


def resolve_guest_binary(demo_name: str, demo_build: Path, manifest: dict, spec: dict) -> Path:
    # Search order: custom-built demo artifacts, then external cache for
    # prebuilt/reference images (Zephyr, Linux).
    candidates = [
        demo_build / demo_name / spec["binary"],
        settings.REPO / "external" / "cache" / "guests" / demo_name / spec["binary"],
    ]
    for c in candidates:
        if c.exists():
            return c
    # External images are fetched explicitly, never as a run side effect.
    hint = (f"\nRun: scripts/task.sh demo fetch {demo_id(demo_name)}"
            if (settings.DEMO_DIR / demo_name / "fetch.sh").exists() else "")
    sys.exit(f"demo_runner: guest binary not found for '{demo_name}': "
             f"tried {', '.join(str(c) for c in candidates)}{hint}")


def _payload_record() -> Callable[..., dict]:
    """Borrow the record builder from the platform payload tool so the record
    schema and its checksum rule keep a single owner."""
    global _make_record
    if _make_record is None:
        path = settings.REPO / "tools" / "payload_manifest.py"
        spec = importlib.util.spec_from_file_location("nova_payload_manifest", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _make_record = module.make_record
    return _make_record


def prepare_payload_manifest(
    demo_name: str,
    demo_build: Path,
    manifest: dict,
) -> Path | None:
    if payload_mode(manifest) != "embedded":
        return None

    make_record = _payload_record()
    records = []
    for index, guest in enumerate(manifest.get("guests", [])):
        binary = resolve_guest_binary(demo_name, demo_build, manifest, guest)
        try:
            records.append(make_record(
                binary,
                guest=index,
                name=guest["name"],
                load_pa=guest["load_addr"],
                entry=guest["entry"],
                memory_size=guest["memory_size"],
            ))
        except ValueError as exc:
            sys.exit(f"[demo_runner] {demo_name}: {exc}")
    if not records:
        raise SystemExit(f"[demo_runner] {demo_name}: embedded mode requires guests")

    path = settings.BUILD_DIR / "payload-manifests" / f"{demo_name}.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    content = f"{json.dumps({'payloads': records}, indent=2)}\n"
    if not path.exists() or path.read_text() != content:
        path.write_text(content)
    return path


def build_qemu_cmd(elf: Path, demo_name: str, demo_build: Path, manifest: dict) -> list[str]:
    validate(demo_name, manifest)
    cmd = [settings.QEMU, *settings.QEMU_BOARD_ARGS, "-kernel", str(elf)]
    for device in manifest.get("qemu_devices", []):
        cmd += ["-device", device]
    if payload_mode(manifest) == "embedded":
        # The payloads travel inside the ELF; QEMU loads nothing extra.
        return cmd
    for guest in manifest.get("guests", []):
        binary = resolve_guest_binary(demo_name, demo_build, manifest, guest)
        cmd += ["-device", f"loader,file={binary},addr={guest['load_addr']:#x},force-raw=on"]
    return cmd
