"""CLI adapter for performance measurement."""

from __future__ import annotations

from typing import Annotated

import typer  # noqa: TID251 — typer stops at this layer

from ..services import firmperf, webperf

app = typer.Typer(
    help="Measure what the interfaces cost.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode=None,
)

Samples = Annotated[
    int,
    typer.Option(
        "--samples",
        min=3,
        max=201,
        help="Feeds per scenario; the median of these is reported.",
    ),
]
AsJson = Annotated[
    bool,
    typer.Option("--json", help="Print the measurement document instead of a table."),
]
Preset = Annotated[
    str,
    typer.Option("--preset", help="Built tree to read. Must already be configured."),
]
Rebuild = Annotated[
    bool,
    typer.Option("--rebuild", help="Build the preset first instead of reading what is there."),
]
Check = Annotated[
    bool,
    typer.Option(
        "--check",
        help=(
            "Exit non-zero when a scenario costs more than a frame or more "
            "than half a core. Off by default: this is a measurement, and a "
            "slower machine is not a regression."
        ),
    ),
]


@app.command()
def web(samples: Samples = 21, as_json: AsJson = False, check: Check = False) -> None:
    """Measure the workbench UI in a browser, against the page itself.

    Each scenario is one batch the bridge really sends — a boot burst, a
    tick of the topics that publish twenty times a second, the window the
    trace strip asks for — fed to the real client over a stubbed socket.
    Script time and the style and layout it leaves behind are reported
    apart, because a fix for one is not a fix for the other.
    """
    code = webperf.measure(samples=samples, as_json=as_json, check=check)
    if code:
        raise typer.Exit(code)


@app.command()
def firmware(
    preset: Preset = "aarch64-release",
    as_json: AsJson = False,
    rebuild: Rebuild = False,
) -> None:
    """Report the built EL2 image's structure: size, edges, reachability.

    Half of a cost. How often each path runs belongs to a run and arrives
    beside this later as its own column — the two are never multiplied,
    because a shared prefix would be counted twice and the instructions a
    path executes depend on the branch it took.

    `unproven` is not dead code. The static rules cannot follow an address
    that was stored rather than branched to, so it names what to review.
    """
    code = firmperf.structure(preset, as_json=as_json, rebuild=rebuild)
    if code:
        raise typer.Exit(code)
