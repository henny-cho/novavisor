"""CLI adapter for the live workbench."""

from __future__ import annotations

from typing import Annotated

import typer

from ..services import manifest
from ..services.workbench import server, session

app = typer.Typer(
    help="Observe and drive the firmware under QEMU.",
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
    str | None,
    typer.Argument(
        metavar="[DEMO]",
        help="Demo ID or directory name to launch on startup.",
        callback=_demo,
    ),
]
Host = Annotated[str, typer.Option(help="Bind address for the UI and WebSocket.")]
Port = Annotated[
    int,
    typer.Option(min=1, max=65535, help="TCP port serving both HTTP and WebSocket."),
]
Variant = Annotated[str | None, typer.Option(metavar="NAME", help="Demo variant name.")]
Verify = Annotated[
    bool,
    typer.Option("--verify", help="Run the verification scenario, streaming its progress."),
]


@app.command()
def serve(
    demo: Demo = None,
    host: Host = "127.0.0.1",
    port: Port = 8787,
    variant: Variant = None,
    verify: Verify = False,
) -> None:
    """Serve the workbench UI against a live QEMU session."""
    target = session.Target(demo=demo, variant=variant, verify=verify) if demo else None
    code = server.serve(host=host, port=port, target=target)
    if code:
        raise typer.Exit(code)
