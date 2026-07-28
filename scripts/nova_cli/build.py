"""CMake build ownership and direct hypervisor execution."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from . import config, process, qemu


@dataclass(frozen=True)
class BuildSpec:
    preset: str
    config_path: Path = config.DEFAULT_CONFIG
    payloads_path: Path = config.DEFAULT_PAYLOADS
    clean: bool = False


def preset_dir(preset: str) -> Path:
    return config.BUILD_ROOT / preset


def selected_preset(*, release: bool, preset: str | None) -> str:
    if preset:
        return preset
    return "aarch64-release" if release else "aarch64-debug"


def spec_from_args(args) -> BuildSpec:
    return BuildSpec(
        preset=selected_preset(release=args.release, preset=args.preset),
        config_path=Path(args.config) if args.config else config.DEFAULT_CONFIG,
        payloads_path=Path(args.payloads) if args.payloads else config.DEFAULT_PAYLOADS,
        clean=args.clean,
    )


def sync_active(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise SystemExit(f"input not found: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or source.read_bytes() != destination.read_bytes():
        shutil.copyfile(source, destination)


def clean() -> None:
    if config.BUILD_ROOT.exists():
        shutil.rmtree(config.BUILD_ROOT)


def build(spec: BuildSpec) -> Path:
    if spec.clean:
        clean()

    output = preset_dir(spec.preset)
    sync_active(spec.config_path, output / "active_config.yml")
    sync_active(spec.payloads_path, output / "active_payloads.yml")

    if not (output / "build.ninja").is_file():
        process.run(["cmake", "--preset", spec.preset])
    process.run(["cmake", "--build", "--preset", spec.preset])
    return output / "novavisor.elf"


def resolve_elf(spec: BuildSpec, *, rebuild: bool) -> Path:
    elf = preset_dir(spec.preset) / "novavisor.elf"
    if rebuild or not elf.is_file():
        elf = build(spec)
    return elf


def run_hypervisor(spec: BuildSpec, *, debug: bool) -> int:
    elf = resolve_elf(spec, rebuild=True)
    command = qemu.board_command(kernel=elf)
    if debug:
        command += ["-s", "-S"]
    return process.call(command)


def inspect_elf(spec: BuildSpec, operation: str) -> int:
    elf = resolve_elf(spec, rebuild=False)
    command = {
        "size": ["aarch64-none-elf-size", str(elf)],
        "objdump": ["aarch64-none-elf-objdump", "-d", "-S", "-C", str(elf)],
    }[operation]
    return process.call(command)
