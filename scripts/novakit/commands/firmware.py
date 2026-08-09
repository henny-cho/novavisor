"""CLI adapter for firmware build, packaging, and verification."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Annotated

import typer  # noqa: TID251 — typer stops at this layer

from ..services import tfa

app = typer.Typer(
    help="Build and verify TF-A firmware.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode=None,
)


def _choices(name: str, values) -> type[Enum]:
    members = {value.upper().replace("-", "_"): value for value in values}
    return Enum(name, members, type=str)


Profile = _choices("Profile", tfa.PROFILES)
Packager = _choices("Packager", tfa.PACKAGERS)
Verifier = _choices("Verifier", tfa.VERIFIERS)
PackagePayload = Annotated[
    Path,
    typer.Option(metavar="FILE", help="BL33 payload to package."),
]
Output = Annotated[
    Path | None,
    typer.Option(metavar="DIR", help="Firmware output directory."),
]
BuildOnly = Annotated[
    bool,
    typer.Option("--build-only", help="Build the chain without launching QEMU."),
]
VerifyPayload = Annotated[
    Path | None,
    typer.Option(metavar="FILE", help="Use an existing BL33 payload."),
]


@app.command()
def build(
    platform: Annotated[Profile, typer.Argument(help="Profile to build.")],
) -> None:
    """Build a BL33 firmware profile."""
    tfa.build_profile(platform.value)


@app.command()
def package(
    platform: Annotated[Packager, typer.Argument(help="Target platform.")],
    payload: PackagePayload,
    output: Output = None,
) -> None:
    """Package a BL33 firmware image."""
    tfa.package_platform(platform.value, payload, output)


@app.command()
def verify(
    platform: Annotated[Verifier, typer.Argument(help="Firmware platform.")],
    build_only: BuildOnly = False,
    output: Output = None,
    payload: VerifyPayload = None,
) -> None:
    """Verify a firmware chain."""
    code = tfa.verify_platform(
        platform.value,
        build_only=build_only,
        payload=payload,
        output_dir=output,
    )
    if code:
        raise typer.Exit(code)
