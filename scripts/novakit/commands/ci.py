"""CLI adapter for local CI lanes."""

from __future__ import annotations

from enum import Enum
from typing import Annotated

import typer

from ..services import ci as service

Lane = Enum(
    "Lane",
    {name.upper(): name for name in (*service.BY_NAME, "all")},
    type=str,
)


def run(
    lane: Annotated[Lane, typer.Argument(help="Lane to run; all runs every lane.")],
) -> None:
    """Run a CI lane locally."""
    code = service.run_lane(lane.value)
    if code:
        raise typer.Exit(code)


def register(root: typer.Typer) -> None:
    root.command("ci")(run)
