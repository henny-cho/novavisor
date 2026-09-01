"""What the firmware costs, reported as two columns and never as one.

The audit that asked for this wanted a cost, and cost is a product: how
many instructions a path holds times how often that path runs. The two
factors come from different places and stay in different columns.

The static column is the built image: how large each routine is, how
many calls leave through a register, how much of the image the rules can
prove is entered. The dynamic column is a run: how often each event
actually fired, re-derived from the records a recording holds.

They are never multiplied. A shared prefix would be counted twice — one
request emits a trap record and then an MMIO record — and the
instructions a path executes depend on the branch it took. Side by side
they answer "which of the paths that are big is also hot", which is the
question, without either half pretending to be the whole.

Two images cannot share a table, so a recording whose run was of a
different build is refused rather than joined.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

from ..core import proc
from ..image import elfstruct, observe
from . import cmake, manifest
from .workbench import events, recording, server, session


class Mismatch(Exception):
    """This recording is not of this image, so the columns cannot join."""


def _summarise(report: dict) -> dict:
    """The few numbers a reader compares between two builds."""
    functions = report["functions"]
    reachable = set(report["reachable"])
    return {
        "functions": len(functions),
        "bytes": sum(functions.values()),
        "reachable": len(reachable),
        "reachable_bytes": sum(size for name, size in functions.items() if name in reachable),
        "unproven": len(report["unproven"]),
        "unproven_bytes": sum(
            size for name, size in functions.items() if name not in reachable
        ),
        "roots": len(report["roots"]),
        "indirect_sites": sum(report["indirect_sites"].values()),
    }


def _label(code: int, arg: int) -> str:
    """A counted key as a reader sees it: the event, then its breakdown.

    The catalogue owns both halves — which event a code is, and which of
    its words the totals were broken down by — so a key printed here
    cannot name a field the records do not hold.
    """
    entry = events.BY_CODE.get(code)
    if entry is None:
        return f"code:{code}"
    return f"{entry.id}={arg:#x}" if entry.group else entry.id


def _dynamic(runs: list[recording.Recording]) -> dict:
    """How often each event fired, across every run that was measured.

    Reported with a spread rather than a single number: repeating a demo
    is how the measurement says which counts are the workload and which
    are the machine it ran on. A run whose trace was not whole is counted
    and marked, never dropped — its counts are a floor, and a floor is
    still evidence.
    """
    totals = [run.totals() for run in runs]
    # Every key every run saw, sampled across all of them: a run that
    # never fired an event contributes a zero rather than a gap, or the
    # spread would describe a different set of runs per row.
    keys = set().union(*(one.events for one in totals))
    per_key = {key: [one.events.get(key, 0) for one in totals] for key in keys}
    return {
        "runs": len(totals),
        "complete": sum(1 for one in totals if one.complete),
        "lost": sum(one.lost for one in totals),
        "events": {
            _label(*key): {
                # median_low, not median: with an even number of runs the
                # midpoint is an average of two counts and no run ever saw
                # it. The same reason the isolation SLO forbids
                # interpolating a quantile — a reported number should be
                # one the machine actually produced.
                "median": statistics.median_low(samples),
                "min": min(samples),
                "max": max(samples),
            }
            for key, samples in sorted(per_key.items(), key=lambda item: -max(item[1]))
        },
    }


def _joinable(runs: list[recording.Recording], elf: Path) -> None:
    """Refuse a recording of another build before its counts reach a table.

    By content, because a path cannot tell two builds apart: a rebuild
    writes the same one. A run that never said which image it was of is
    refused for the same reason a mismatched one is — the table would
    claim a join it cannot support.
    """
    want = observe.image_id(elf)
    for run in runs:
        if not run.image:
            raise Mismatch(
                f"{run.directory.name} does not name the image it ran, so its counts "
                "cannot be joined to this one; re-record it"
            )
        if run.image != want:
            raise Mismatch(
                f"{run.directory.name} ran image {run.image[:12]}, not {want[:12]}: "
                "two builds cannot share one table"
            )


def _table(preset: str, report: dict) -> str:
    counts = _summarise(report)
    width = max(len(key) for key in counts)
    lines = [f"{preset}", "  static"]
    lines += [f"    {key:<{width}}  {value}" for key, value in counts.items()]
    lines.append(
        "    unproven is not dead code: the static rules cannot follow a stored "
        "address, so it is a set to review."
    )
    dynamic = report.get("dynamic")
    if dynamic is None:
        return "\n".join(lines)
    lines.append(
        f"  dynamic  {dynamic['runs']} run(s), {dynamic['complete']} with a whole trace, "
        f"{dynamic['lost']} record(s) lost"
    )
    if dynamic["complete"] < dynamic["runs"]:
        lines.append("    counts from an incomplete run are a floor, not a measurement.")
    name_width = max((len(name) for name in dynamic["events"]), default=0)
    lines += [
        f"    {name:<{name_width}}  {spread['median']:>8}  "
        f"[{spread['min']}..{spread['max']}]"
        for name, spread in dynamic["events"].items()
    ]
    lines.append("    the two columns are never multiplied: a shared prefix counts twice.")
    return "\n".join(lines)


def report(preset: str, *, rebuild: bool = False, recorded: Path | None = None) -> dict:
    """One built image, and a run of it when one is offered.

    Returns rather than prints, so the same answer can be rendered and
    compared: a report that could only be read as text would have to be
    parsed back to ask whether reading it twice said the same thing.
    """
    elf = cmake.resolve_elf(cmake.BuildSpec.of(preset=preset), rebuild=rebuild)
    try:
        found = elfstruct.analyse(elf).as_dict()
    except elfstruct.ContractViolation as refusal:
        raise SystemExit(f"cannot analyse {preset}: {refusal}") from None
    if recorded is not None:
        try:
            runs = recording.load_all(recorded)
            _joinable(runs, elf)
        except (recording.Unreadable, Mismatch, OSError) as refusal:
            raise SystemExit(f"cannot read {recorded}: {refusal}") from None
        found["dynamic"] = _dynamic(runs)
    return {"preset": preset, **found}


def structure(
    preset: str,
    *,
    as_json: bool = False,
    rebuild: bool = False,
    recorded: Path | None = None,
) -> int:
    """Print what `report` found, as a table or as the document itself."""
    found = report(preset, rebuild=rebuild, recorded=recorded)
    print(json.dumps(found, indent=2) if as_json else _table(found["preset"], found))
    return 0


def demo_preset(demo: str) -> str:
    """The composition this demo runs on, which its manifest owns.

    Asked rather than chosen: which components a run carries is a
    property of the variant, so a measurement of a demo is a measurement
    of that image and the static column has to follow it there.
    """
    name = manifest.resolve_demo(demo)
    _, demo_manifest = manifest.load_manifest(name)
    return manifest.manifest_preset(demo_manifest)


def measure(demo: str, *, runs: int) -> Path:
    """Record `runs` runs of one demo and hand back where they landed.

    Under the preset's own build directory rather than a temporary one:
    a measurement is evidence, and the next one is told the previous is
    still there rather than silently replacing it.
    """
    into = cmake.preset_dir(demo_preset(demo)) / "measurements" / demo
    try:
        server.measure(target=session.Target(demo=demo), record=into, runs=runs)
    except FileExistsError as taken:
        raise SystemExit(
            f"{taken}: it holds a measurement already; move or remove it to take another"
        ) from None
    return into


def gate() -> int:
    """The lane's step: run the analyser's own suite against built images.

    The suite is the single definition of what the analysis must answer —
    the oracle comparison, the determinism check, the refusal of a
    stripped image, and which presets ship. Named explicitly here because
    the host lane has no cross toolchain and skips every case that needs
    one; this lane builds the images, so the same module runs for real.
    """
    proc.run([sys.executable, "-m", "unittest", "-v", "tests.image.elfstruct_test"])
    return 0
