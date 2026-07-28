"""CMake preset ownership: what to build, where, and with which inputs."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from ..core import config, proc


def selected_preset(*, release: bool = False, preset: str | None = None) -> str:
    if preset:
        return preset
    return "aarch64-release" if release else "aarch64-debug"


def _require(path: Path, what: str) -> Path:
    if not path.exists():
        raise SystemExit(f"nova: {what} not found: {path}")
    return path


@dataclass(frozen=True)
class BuildSpec:
    preset: str
    config_path: Path = config.DEFAULT_CONFIG
    payloads_path: Path = config.DEFAULT_PAYLOADS
    clean: bool = False

    @classmethod
    def of(
        cls,
        *,
        preset: str | None = None,
        release: bool = False,
        config_path: Path | str | None = None,
        payloads_path: Path | str | None = None,
        clean: bool = False,
    ) -> "BuildSpec":
        """A spec from user-supplied choices, rejecting inputs that do not exist.

        Omitting an input restores its default, so one demo's choice never
        leaks into the next run.
        """
        return cls(
            preset=selected_preset(release=release, preset=preset),
            config_path=_require(
                config.DEFAULT_CONFIG if config_path is None else Path(config_path),
                "guest config",
            ),
            payloads_path=_require(
                config.DEFAULT_PAYLOADS if payloads_path is None else Path(payloads_path),
                "payload manifest",
            ),
            clean=clean,
        )


def preset_dir(preset: str) -> Path:
    return config.BUILD_ROOT / preset


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
    # A no-change Ninja rebuild is nearly free, while skipping on ELF
    # existence would verify against a binary older than the sources.
    if spec.clean:
        clean()

    output = preset_dir(spec.preset)
    sync_active(spec.config_path, output / "active_config.yml")
    sync_active(spec.payloads_path, output / "active_payloads.yml")

    if not (output / "build.ninja").is_file():
        proc.run(["cmake", "--preset", spec.preset])
    proc.run(["cmake", "--build", "--preset", spec.preset])
    return output / "novavisor.elf"


def resolve_elf(spec: BuildSpec, *, rebuild: bool) -> Path:
    elf = preset_dir(spec.preset) / "novavisor.elf"
    if rebuild or not elf.is_file():
        elf = build(spec)
    return elf
