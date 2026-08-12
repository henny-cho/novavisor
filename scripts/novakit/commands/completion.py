"""CLI adapter for shell completion installation."""

from __future__ import annotations

from typing import Annotated

import typer  # noqa: TID251 — typer stops at this layer
from typer.completion import (  # noqa: TID251
    Shells,
    _get_shell_name,
    get_completion_script,
)

from ..services import completion as service

app = typer.Typer(
    help="Configure Nova shell integration.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode=None,
)


def _selected_shell(shell: Shells | None) -> str:
    selected = shell.value if shell else _get_shell_name()
    if selected is None:
        raise typer.BadParameter("could not detect the shell; pass --shell NAME")
    return selected


def _report(changes: tuple[service.Change, ...]) -> None:
    for change in changes:
        typer.echo(f"[completion] {change.action}: {change.path}: {change.detail}")


@app.command()
def install(
    shell: Annotated[
        Shells | None,
        typer.Option(metavar="NAME", help="Shell to configure; auto-detect when omitted."),
    ] = None,
) -> None:
    """Add Nova to PATH and install tab completion."""
    selected = _selected_shell(shell)
    script = get_completion_script(
        prog_name="nova",
        complete_var="_NOVA_COMPLETE",
        shell=selected,
    )
    _report(service.install(selected, script))
    typer.echo("Completion will take effect once you restart the terminal")


@app.command()
def uninstall(
    shell: Annotated[
        Shells | None,
        typer.Option(metavar="NAME", help="Shell to clean; auto-detect when omitted."),
    ] = None,
) -> None:
    """Remove Nova's PATH and tab completion setup."""
    _report(service.uninstall(_selected_shell(shell)))
    typer.echo("Changes will take effect once you restart the terminal")
