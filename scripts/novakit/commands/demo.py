"""CLI adapter for demo catalog, execution, and verification."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer  # noqa: TID251 — typer stops at this layer

from ..services import demo, manifest

app = typer.Typer(
    help="Manage and verify demo guests.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode=None,
)


def _demo(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return manifest.resolve_demo(value)
    except SystemExit as error:
        raise typer.BadParameter(str(error)) from error


Demo = Annotated[
    str,
    typer.Argument(metavar="DEMO", help="Demo ID or directory name.", callback=_demo),
]
OptionalDemo = Annotated[
    str | None,
    typer.Argument(
        metavar="[DEMO]",
        help="Demo ID or directory name.",
        callback=_demo,
    ),
]
AllDemos = Annotated[
    bool,
    typer.Option("--all", help="Operate on every enabled demo."),
]
Artifacts = Annotated[
    Path | None,
    typer.Option(metavar="DIR", help="Write failure artifacts under this directory."),
]
Debug = Annotated[
    bool,
    typer.Option("--debug", help="Halt the CPU and expose a GDB server on port 1234."),
]
Runs = Annotated[
    int,
    typer.Option(
        min=1,
        max=100,
        metavar="N",
        help="Number of verification attempts (1-100).",
    ),
]
Summary = Annotated[
    Path | None,
    typer.Option(metavar="CSV", help="Write per-attempt results to a CSV file."),
]


def _finish(code: int) -> None:
    if code:
        raise typer.Exit(code)


def _scope(name: str | None, all_demos: bool) -> str | None:
    if (name is None) == (not all_demos):
        raise typer.BadParameter("provide exactly one of DEMO or --all")
    return name


@app.command("list")
def list_demos() -> None:
    """List demos and their status."""
    _finish(demo.list_demos())


@app.command()
def build() -> None:
    """Build in-tree guests."""
    _finish(demo.build())


@app.command()
def fetch(name: OptionalDemo = None, all_demos: AllDemos = False) -> None:
    """Populate external image caches."""
    _finish(demo.fetch(_scope(name, all_demos), all_demos=all_demos))


@app.command()
def run(
    name: Demo,
    debug: Debug = False,
) -> None:
    """Launch one demo interactively."""
    _finish(demo.run(name, debug=debug))


@app.command()
def verify(
    name: OptionalDemo = None,
    all_demos: AllDemos = False,
    artifacts: Artifacts = None,
) -> None:
    """Check expected output from one or every enabled demo."""
    selected = _scope(name, all_demos)
    code = (
        demo.verify_all(artifacts)
        if all_demos
        else demo.verify_one(selected, artifacts)
    )
    _finish(code)


@app.command()
def soak(
    name: Demo,
    runs: Runs,
    summary: Summary = None,
    artifacts: Artifacts = None,
) -> None:
    """Repeat verification of one demo."""
    _finish(demo.soak(name, runs, summary=summary, artifact_dir=artifacts))
