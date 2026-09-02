"""A manifest turned into the artifacts a run needs.

The hypervisor ELF, the guest binaries, an optional embedded-payload
manifest, the QEMU argv — and the scenario that ties them to what the run
must print.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from ..core import board, config, proc
from ..image import observe
from ..image.payload import make_record
from . import cmake, expect
from . import manifest as manifests


def build_demos() -> Path:
    # Configure once; rebuild is cheap.
    demo_build = config.DEMO_BUILD_DIR
    if not (demo_build / "build.ninja").exists():
        demo_build.mkdir(parents=True, exist_ok=True)
        proc.run(
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
    proc.run(["cmake", "--build", str(demo_build)])
    return demo_build


def resolve_guest_binary(demo_name: str, demo_build: Path, spec: dict) -> Path:
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
        f"\nRun: ./nova demo fetch {manifests.demo_id(demo_name)}"
        if (config.DEMO_DIR / demo_name / "fetch.sh").exists()
        else ""
    )
    sys.exit(
        f"nova demo: guest binary not found for '{demo_name}': "
        f"tried {', '.join(str(c) for c in candidates)}{hint}"
    )


def prepare_payload_manifest(
    demo_name: str,
    demo_build: Path,
    manifest: dict,
) -> Path | None:
    if manifests.payload_mode(manifest) != "embedded":
        return None

    records = []
    for index, guest in enumerate(manifest.get("guests", [])):
        binary = resolve_guest_binary(demo_name, demo_build, guest)
        records.append(make_record(
            binary,
            guest=index,
            name=guest["name"],
            load_pa=guest["load_addr"],
            entry=guest["entry"],
            memory_size=guest["memory_size"],
        ))
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


def build_qemu_cmd(
    elf: Path,
    demo_name: str,
    demo_build: Path,
    manifest: dict,
    variant: dict | None = None,
) -> list[str]:
    manifests.validate(demo_name, manifest)
    cmd = board.command(kernel=elf)
    for device in manifests.manifest_devices(manifest, variant or {}):
        cmd += ["-device", device]
    if manifests.payload_mode(manifest) == "embedded":
        # The payloads travel inside the ELF; QEMU loads nothing extra.
        return cmd
    for guest in manifest.get("guests", []):
        binary = resolve_guest_binary(demo_name, demo_build, guest)
        cmd += ["-device", f"loader,file={binary},addr={guest['load_addr']:#x},force-raw=on"]
    return cmd


def copy_image(source: Path, destination: Path) -> tuple[Path, ...]:
    """Copy an image and everything that answers questions about it.

    An image is not only its ELF. The observation view sits beside it
    under a name only `observe.artifact_of` decides, and `observe` and
    `walk` steps read an image through it — one that travelled alone is
    an image no such step can read.

    A source without a view copies the ELF alone rather than inventing
    one; `observe.view_of` then reports the real absence by name.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    copied = [destination]
    view = observe.artifact_of(source)
    if view.is_file():
        beside = observe.artifact_of(destination)
        shutil.copy2(view, beside)
        copied.append(beside)
    return tuple(copied)


def scenario_for(
    name: str,
    demo_manifest: dict,
    variant: dict,
    *,
    demo_build: Path | None = None,
    elf_snapshot: Path | None = None,
) -> expect.Scenario:
    """Build everything the variant needs and state what its run must do."""
    forbidden = manifests.manifest_pattern_list(demo_manifest, "forbid")
    steps = tuple(variant.get("steps", []))
    if forbidden and not steps:
        raise SystemExit("[nova demo] manifest 'forbid' requires steps")

    if demo_build is None:
        demo_build = build_demos()
    payloads = prepare_payload_manifest(name, demo_build, demo_manifest)
    guest_config = variant.get("config")
    elf = cmake.build(cmake.BuildSpec.of(
        preset=manifests.variant_preset(variant),
        config_path=None if guest_config is None else config.REPO / guest_config,
        payloads_path=payloads,
    ))
    if elf_snapshot is not None:
        # A soak rebuilds between attempts; the snapshot keeps the exact
        # image an attempt ran so the evidence matches the failure.
        copy_image(elf, elf_snapshot)
        elf = elf_snapshot

    return expect.Scenario(
        label=name if "name" not in variant else f"{name}[{variant['name']}]",
        phase=demo_manifest.get("phase"),
        command=tuple(build_qemu_cmd(elf, name, demo_build, demo_manifest, variant)),
        timeout_seconds=int(demo_manifest.get("timeout_seconds", 30)),
        steps=steps,
        forbidden_patterns=forbidden,
        elf=elf,
        expects_panic=bool(demo_manifest.get("expects_panic", False)),
    )
