"""CLI adapter for local CI lanes."""

from __future__ import annotations

from enum import Enum
from typing import Annotated

import typer  # noqa: TID251 — typer stops at this layer

from ..services import ci as service

Lane = Enum(
    "Lane",
    {name.upper(): name for name in (*service.ALL_METADATA_LANES, "all")},
    type=str,
)


def run(
    lane: Annotated[Lane, typer.Argument(help="Lane to run; all runs every lane.")],
    metadata: Annotated[
        bool,
        typer.Option("--metadata", help="Print lane environment and cache metadata for CI."),
    ] = False,
) -> None:
    """Run a CI lane locally, or inspect its metadata."""
    if metadata:
        if lane.value == "all":
            raise typer.BadParameter("--metadata cannot be used with 'all'")
        data = service.lane_metadata(lane.value)
        for key, value in data.items():
            print(f"{key}={value}")
        return

    code = service.run_lane(lane.value)
    if code:
        raise typer.Exit(code)


def register(root: typer.Typer) -> None:
    root.command("ci")(run)
