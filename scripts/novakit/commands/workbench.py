"""CLI adapter for the live workbench."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer

from ..services import manifest
from ..services.workbench import client, hardware, history, server, session, trace

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
TraceHistory = Annotated[
    int,
    typer.Option(
        "--trace-history",
        metavar="RECORDS",
        min=1024,
        help=(
            "Drained trace records the bridge keeps, at 32 bytes each. "
            f"The default {history.DEFAULT_CAPACITY} is 16 MiB, which the measured "
            "~1500 events/s fills in about six minutes; a busier run fills it sooner, "
            "and the summary publishes the span actually held."
        ),
    ),
]


@app.command()
def serve(
    demo: Demo = None,
    host: Host = "127.0.0.1",
    port: Port = 8787,
    variant: Variant = None,
    verify: Verify = False,
    trace_history: TraceHistory = history.DEFAULT_CAPACITY,
) -> None:
    """Serve the workbench UI against a live QEMU session."""
    target = session.Target(demo=demo, variant=variant, verify=verify) if demo else None
    code = server.serve(host=host, port=port, target=target, trace_history=trace_history)
    if code:
        raise typer.Exit(code)


Limit = Annotated[int, typer.Option(min=1, max=4096, help="How many of the newest records to print.")]
Follow = Annotated[
    bool,
    typer.Option("--follow", "-f", help="Keep printing records as they arrive."),
]
Since = Annotated[
    float,
    typer.Option(
        "--since",
        metavar="SECONDS",
        min=0.0,
        help=(
            "How far back to start, against a running bridge. A wider stretch "
            "than a terminal can list comes back as a count instead of lines. "
            "Ignored without a bridge: the rings hold only what they hold."
        ),
    ),
]


@app.command("trace")
def show_trace(limit: Limit = 40, follow: Follow = False, since: Since = 5.0) -> None:
    """Print a live session's trace records to the terminal.

    The CLI twin of the T layer, and — when a bridge is running — a
    reader of the same history the browser draws from. Two consumers
    reading the firmware's rings with two cursors would answer
    differently about one run, and the rings hold seconds where the
    bridge holds minutes.

    With no bridge it falls back to the rings themselves, which is what
    makes this work with no browser and no image at all: the region
    describes its own geometry.
    """
    ports = sorted(Path("/dev/shm").glob("nova-wb-*/port"))
    if ports:
        code = client.tail(int(ports[-1].read_text()), since, limit, follow)
        if code:
            raise typer.Exit(code)
        return
    surfaces = sorted(Path("/dev/shm").glob("nova-wb-*/guest-ram"))
    if not surfaces:
        raise typer.Exit(
            code=_no_session("no workbench session is running (nova workbench serve ...)")
        )
    if follow:
        # Said rather than silently ignored: the rings have no history
        # to seek in and no notification to wait on, so these options
        # describe something this path cannot do.
        _no_session("--follow needs a running bridge; showing the rings once")
    board = hardware.platform()
    code = trace.report(
        surfaces[-1], board["NOVA_BOARD_PHYS_RAM_BASE"], board["NOVA_BOARD_TRACE_PA"], limit
    )
    if code:
        raise typer.Exit(code)


def _no_session(message: str) -> int:
    print(f"[workbench] trace: {message}", file=sys.stderr)
    return 1
