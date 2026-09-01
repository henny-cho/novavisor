"""What the firmware costs, reported as two things and never as one.

The audit that asked for this wanted a cost, and cost is a product: how
many instructions a path holds times how often that path runs. Only the
first factor is here. The second is a run's business and arrives beside
it later; the two stay separate columns because multiplying them would
double-count a shared prefix — the same request emits a trap record and
then an MMIO record — and because the instructions a path actually
executes depend on the branch it took.

So this prints structure: how large each routine is, how many calls
leave through a register, and how much of the image the static rules can
prove is entered. A regression in any of those is a fact about the build,
which is what a report can honestly claim without a clock.
"""

from __future__ import annotations

import json
import sys

from ..core import proc
from ..image import elfstruct
from . import cmake


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


def _table(preset: str, report: dict) -> str:
    counts = _summarise(report)
    width = max(len(key) for key in counts)
    lines = [f"{preset}"]
    lines += [f"  {key:<{width}}  {value}" for key, value in counts.items()]
    lines.append(
        "  unproven is not dead code: the static rules cannot follow a stored "
        "address, so it is a set to review."
    )
    return "\n".join(lines)


def structure(preset: str, *, as_json: bool = False, rebuild: bool = False) -> int:
    """Report one built image's structure, or say why it cannot be read."""
    elf = cmake.resolve_elf(cmake.BuildSpec.of(preset=preset), rebuild=rebuild)
    try:
        report = elfstruct.analyse(elf).as_dict()
    except elfstruct.ContractViolation as refusal:
        raise SystemExit(f"cannot analyse {preset}: {refusal}") from None
    print(json.dumps({"preset": preset, **report}, indent=2) if as_json else _table(preset, report))
    return 0


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
