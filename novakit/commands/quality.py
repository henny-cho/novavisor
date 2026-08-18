"""Source quality commands: formatting, static analysis, and host tests."""

from __future__ import annotations

from typing import Annotated

import typer  # noqa: TID251 — typer stops at this layer

from ..services import gates

Check = Annotated[
    bool,
    typer.Option(
        "--check",
        help="Report formatting differences without changing files.",
    ),
]


def _finish(code: int) -> None:
    if code:
        raise typer.Exit(code)


def format_sources(check: Check = False) -> None:
    """Format C and C++ sources."""
    _finish(gates.format_sources(check=check))


def lint() -> None:
    """Run clang-tidy."""
    _finish(gates.lint())


def test() -> None:
    """Build and run host tests."""
    _finish(gates.test())


def register(root: typer.Typer) -> None:
    root.command("format")(format_sources)
    root.command()(lint)
    root.command()(test)
