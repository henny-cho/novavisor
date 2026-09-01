"""CLI adapter for performance measurement."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer  # noqa: TID251 — typer stops at this layer

from ..services import firmperf, webperf

# The image a report describes when nobody named one and no demo picked
# for it: the composition that ships.
DEFAULT_PRESET = "aarch64-release"

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
    str | None,
    typer.Option(
        "--preset",
        help=(
            "Built tree to read. Must already be configured. With --demo the "
            "demo's own manifest decides, and naming a different one is refused."
        ),
    ),
]
Rebuild = Annotated[
    bool,
    typer.Option("--rebuild", help="Build the preset first instead of reading what is there."),
]
Demo = Annotated[
    str | None,
    typer.Option("--demo", help="Run this demo and measure it, instead of reading a recording."),
]
Runs = Annotated[
    int,
    typer.Option("--runs", min=1, max=64, help="How many times to run the demo. Needs --demo."),
]
Recorded = Annotated[
    Path | None,
    typer.Option(
        "--recording",
        exists=True,
        file_okay=False,
        help="A directory written by `workbench serve --record`, to read counts from.",
    ),
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
    preset: Preset = None,
    as_json: AsJson = False,
    rebuild: Rebuild = False,
    demo: Demo = None,
    runs: Runs = 1,
    recording: Recorded = None,
) -> None:
    """Report the built EL2 image's structure, and a run of it if offered.

    Two columns, never multiplied: a shared prefix would be counted twice
    — one request emits a trap record and then an MMIO record — and the
    instructions a path executes depend on the branch it took. Side by
    side they say which of the big paths is also hot.

    With no run named this reports the image alone. `--demo` runs it and
    measures what it does; `--recording` reads a run somebody already
    recorded. A recording of a different build is refused: two images
    cannot share one table.

    `unproven` is not dead code. The static rules cannot follow an address
    that was stored rather than branched to, so it names what to review.
    """
    if demo is not None and recording is not None:
        raise typer.BadParameter("--demo runs a measurement and --recording reads one; pick one")
    if runs != 1 and demo is None:
        raise typer.BadParameter("--runs says how many times to run --demo, which was not given")
    if demo is not None:
        # The variant owns which composition runs, so the static column
        # follows the run there rather than describing another build.
        ran = firmperf.demo_preset(demo)
        if preset is not None and preset != ran:
            raise typer.BadParameter(f"{demo} runs on {ran}, not {preset}")
        preset = ran
        recording = firmperf.measure(demo, runs=runs)
    code = firmperf.structure(
        preset or DEFAULT_PRESET, as_json=as_json, rebuild=rebuild, recorded=recording
    )
    if code:
        raise typer.Exit(code)
