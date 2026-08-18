"""Typer application composition for the public automation CLI."""

from __future__ import annotations

import io
import sys

import typer  # noqa: TID251 — the CLI boundary lives here
from typer.completion import completion_init  # noqa: TID251

from .commands import ci, completion, demo, firmware, quality, workbench, workspace

completion_init()

app = typer.Typer(
    name="nova",
    help="NovaVisor automation",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode=None,
    pretty_exceptions_enable=False,
)

workspace.register(app)
quality.register(app)
app.add_typer(completion.app, name="completion")
app.add_typer(demo.app, name="demo")
app.add_typer(firmware.app, name="firmware")
app.add_typer(workbench.app, name="workbench")
ci.register(app)


def main() -> None:
    # Keep stdout and stderr in causal order when CI kills a timed-out job.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)
    app(prog_name="nova")
