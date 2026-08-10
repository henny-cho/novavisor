"""CLI adapter for the live workbench."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Annotated

import typer  # noqa: TID251 — typer stops at this layer

from ..services import cmake, manifest
from ..services.workbench import (
    client,
    commands,
    hardware,
    history,
    server,
    session,
    steps,
    trace,
)

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
            f"The default {history.DEFAULT_CAPACITY} is 16 MiB. How long that "
            "reaches back depends on the run's event rate, so the trace summary "
            "publishes the span actually held rather than promising a duration."
        ),
    ),
]


Record = Annotated[
    Path | None,
    typer.Option(
        "--record",
        metavar="DIR",
        help=(
            "Write the run to DIR as the wire saw it, for replay without "
            "QEMU or an image. Explicit, and never on by default: a busy "
            "twenty-minute run is a few hundred megabytes, and the size is "
            "reported on exit."
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
    record: Record = None,
) -> None:
    """Serve the workbench UI against a live QEMU session."""
    target = session.Target(demo=demo, variant=variant, verify=verify) if demo else None
    code = server.serve(
        host=host, port=port, target=target, trace_history=trace_history, record=record
    )
    if code:
        raise typer.Exit(code)


RecordingDir = Annotated[
    Path,
    typer.Argument(metavar="DIR", help="A directory written by `serve --record`."),
]


@app.command()
def replay(
    directory: RecordingDir,
    host: Host = "127.0.0.1",
    port: Port = 8787,
) -> None:
    """Serve a recorded session — no QEMU, no image, no toolchain.

    The same UI answered by the same bridge code, so one run has one
    answer; a separate replay path would be free to differ from it.
    """
    code = server.replay(host=host, port=port, directory=directory)
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
        surfaces[-1],
        board["NOVA_BOARD_PHYS_RAM_BASE"],
        board["NOVA_BOARD_TRACE_PA"],
        board["NOVA_BOARD_TRACE_SIZE"],
        limit,
    )
    if code:
        raise typer.Exit(code)


Op = Annotated[
    str,
    typer.Argument(
        metavar="OP [ARG...]",
        help=(
            "One command as the run advertises it, arguments included: "
            "`stop 0`. The vocabulary comes from the page the machine "
            "published, so a build that carries no such op says so."
        ),
    ),
]
Wait = Annotated[
    float,
    typer.Option("--wait", metavar="SECONDS", min=0.1,
                 help="How long to wait for the verdict."),
]


@app.command("command")
def issue_command(op: Op, wait: Wait = 10.0) -> None:
    """Drive a running machine from the terminal.

    The hand-driven twin of a scenario's `command` step, and the way a
    CI failure is reproduced without a browser. It shares the step's
    handler, so the wait for the ring to be advertised and the reading
    of the verdict are the same code.

    Refused while a bridge is running. The ring has one write cursor and
    a bridge holds it for the UI; a second writer would not queue behind
    it, it would race it.
    """
    if sorted(Path("/dev/shm").glob("nova-wb-*/port")):
        raise typer.Exit(code=_no_session(
            "a bridge is running and holds the ring's write cursor; "
            "drive it from the workbench UI instead", scope="command"))
    surfaces = sorted(Path("/dev/shm").glob("nova-wb-*/guest-ram"))
    if not surfaces:
        raise typer.Exit(code=_no_session(
            "no machine is running (nova demo run ...)", scope="command"))

    machine = steps.Machine(cmake.default_image(), surfaces[-1])
    try:
        answer, said = steps.carry_out(machine, op, wait, time.sleep)
    finally:
        machine.close()
    # The exit status is the machine's own result code, so a script
    # reading it and a person reading the line are told the same thing
    # and no second numbering has to be kept in step with the header.
    code = commands.RESULTS.get(answer, 1)
    print(f"[command] {said}", file=sys.stderr if code else sys.stdout)
    if code:
        raise typer.Exit(code)


def _no_session(message: str, *, scope: str = "trace") -> int:
    print(f"[workbench] {scope}: {message}", file=sys.stderr)
    return 1
