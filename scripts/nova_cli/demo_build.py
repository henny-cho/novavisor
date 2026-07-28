"""Build steps and QEMU command construction.

Turns a manifest into the artifacts a run needs: the hypervisor ELF, the demo
guest binaries, an optional embedded-payload manifest, and the QEMU argv.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Callable

from . import build as core_build
from . import config, process, qemu
from .manifest import demo_id, payload_mode, validate

_make_record = None


def build_hypervisor(
    guest_config: str | None = None,
    payloads: Path | None = None,
) -> Path:
    # A no-change Ninja rebuild is nearly free, while skipping on ELF
    # existence would verify against a stale binary after source edits.
    # Omitting config/payloads restores the defaults, so one demo's
    # choice never leaks into the next.
    cfg = (
        config.DEFAULT_CONFIG
        if guest_config is None
        else config.REPO / guest_config
    )
    if not cfg.exists():
        sys.exit(f"nova demo: guest config not found: {cfg}")
    payload_manifest = payloads if payloads is not None else config.DEFAULT_PAYLOADS
    if not payload_manifest.exists():
        sys.exit(f"nova demo: payload manifest not found: {payload_manifest}")

    return core_build.build(
        core_build.BuildSpec(
            preset=config.HV_PRESET,
            config_path=cfg,
            payloads_path=payload_manifest,
        )
    )


def build_demos() -> Path:
    # Configure once; rebuild is cheap.
    demo_build = config.DEMO_BUILD_DIR
    if not (demo_build / "build.ninja").exists():
        demo_build.mkdir(parents=True, exist_ok=True)
        process.run(
            [
                "cmake",
                "-S",
                str(config.DEMO_DIR),
                "-B",
                str(demo_build),
                "-G",
                "Ninja",
                "-DCMAKE_C_COMPILER=aarch64-none-elf-gcc",
                "-DCMAKE_ASM_COMPILER=aarch64-none-elf-gcc",
                "-DCMAKE_SYSTEM_NAME=Generic",
                "-DCMAKE_SYSTEM_PROCESSOR=aarch64",
                "-DCMAKE_TRY_COMPILE_TARGET_TYPE=STATIC_LIBRARY",
            ]
        )
    process.run(["cmake", "--build", str(demo_build)])
    return demo_build


def resolve_guest_binary(demo_name: str, demo_build: Path, manifest: dict, spec: dict) -> Path:
    # Search order: custom-built demo artifacts, then external cache for
    # prebuilt/reference images (Zephyr, Linux).
    candidates = [
        demo_build / demo_name / spec["binary"],
        config.REPO / "external" / "cache" / "guests" / demo_name / spec["binary"],
    ]
    for c in candidates:
        if c.exists():
            return c
    # External images are fetched explicitly, never as a run side effect.
    hint = (
        f"\nRun: scripts/nova demo fetch {demo_id(demo_name)}"
        if (config.DEMO_DIR / demo_name / "fetch.sh").exists()
        else ""
    )
    sys.exit(
        f"nova demo: guest binary not found for '{demo_name}': "
        f"tried {', '.join(str(c) for c in candidates)}{hint}"
    )


def payload_record() -> Callable[..., dict]:
    """Borrow the record builder from the platform payload tool so the record
    schema and its checksum rule keep a single owner."""
    global _make_record
    if _make_record is None:
        path = config.REPO / "tools" / "payload_manifest.py"
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

    make_record = payload_record()
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
            sys.exit(f"[nova demo] {demo_name}: {exc}")
    if not records:
        raise SystemExit(f"[nova demo] {demo_name}: embedded mode requires guests")

    path = config.BUILD_ROOT / "payload-manifests" / f"{demo_name}.yml"
    write_payload_manifest(path, records)
    return path


def write_payload_manifest(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = f"{json.dumps({'payloads': records}, indent=2)}\n"
    if not path.exists() or path.read_text() != content:
        path.write_text(content)


def build_qemu_cmd(elf: Path, demo_name: str, demo_build: Path, manifest: dict) -> list[str]:
    validate(demo_name, manifest)
    cmd = qemu.board_command(kernel=elf)
    for device in manifest.get("qemu_devices", []):
        cmd += ["-device", device]
    if payload_mode(manifest) == "embedded":
        # The payloads travel inside the ELF; QEMU loads nothing extra.
        return cmd
    for guest in manifest.get("guests", []):
        binary = resolve_guest_binary(demo_name, demo_build, manifest, guest)
        cmd += ["-device", f"loader,file={binary},addr={guest['load_addr']:#x},force-raw=on"]
    return cmd
