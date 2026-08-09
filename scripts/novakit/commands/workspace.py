"""Primary workspace commands: build, run, clean, and inspect."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from ..services import cmake, workspace

Release = Annotated[
    bool,
    typer.Option("--release", help="Use the aarch64-release preset."),
]
Preset = Annotated[
    str | None,
    typer.Option(metavar="NAME", help="Use an explicit CMake preset."),
]
Config = Annotated[
    Path | None,
    typer.Option(
        metavar="FILE",
        help="Use a guest configuration instead of configs/default.yml.",
    ),
]
Payloads = Annotated[
    Path | None,
    typer.Option(
        metavar="FILE",
        help="Use a payload manifest instead of configs/payloads.yml.",
    ),
]
Clean = Annotated[
    bool,
    typer.Option("--clean", help="Remove the build tree before building."),
]
Debug = Annotated[
    bool,
    typer.Option("--debug", help="Halt the CPU and expose a GDB server on port 1234."),
]

inspect_app = typer.Typer(
    help="Inspect the hypervisor image.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode=None,
)


def _spec(
    release: bool,
    preset: str | None,
    config: Path | None,
    payloads: Path | None,
    *,
    clean: bool = False,
) -> cmake.BuildSpec:
    if release and preset:
        raise typer.BadParameter("--release and --preset cannot be used together")
    return cmake.BuildSpec.of(
        preset=preset,
        release=release,
        config_path=config,
        payloads_path=payloads,
        clean=clean,
    )


def _finish(code: int) -> None:
    if code:
        raise typer.Exit(code)


def build(
    release: Release = False,
    preset: Preset = None,
    config: Config = None,
    payloads: Payloads = None,
    clean: Clean = False,
) -> None:
    """Build the hypervisor."""
    _finish(workspace.build(_spec(release, preset, config, payloads, clean=clean)))


def run(
    release: Release = False,
    preset: Preset = None,
    config: Config = None,
    payloads: Payloads = None,
    debug: Debug = False,
) -> None:
    """Run the hypervisor under QEMU."""
    _finish(workspace.run(_spec(release, preset, config, payloads), debug=debug))


def clean() -> None:
    """Remove the build tree."""
    _finish(workspace.clean())


@inspect_app.command("size")
def size(
    release: Release = False,
    preset: Preset = None,
    config: Config = None,
    payloads: Payloads = None,
) -> None:
    """Report section sizes."""
    _finish(workspace.inspect(_spec(release, preset, config, payloads), "size"))


@inspect_app.command("symbols")
def symbols(
    release: Release = False,
    preset: Preset = None,
    config: Config = None,
    payloads: Payloads = None,
) -> None:
    """Resolve the workbench observation manifest against the image."""
    _finish(workspace.inspect_symbols(_spec(release, preset, config, payloads)))


@inspect_app.command("disassemble")
def disassemble(
    release: Release = False,
    preset: Preset = None,
    config: Config = None,
    payloads: Payloads = None,
) -> None:
    """Disassemble the image with source lines."""
    _finish(workspace.inspect(_spec(release, preset, config, payloads), "disassemble"))


def register(root: typer.Typer) -> None:
    root.command()(build)
    root.command()(run)
    root.command()(clean)
    root.add_typer(inspect_app, name="inspect")
