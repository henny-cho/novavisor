"""Paths through the machine, and what would count as evidence for one.

Which blocks a path joins is structure — the same kind of fact as an
address or an interrupt number — so it travels the way the rest of the
board map travels. Keeping the table here also puts the badge names next
to the taxonomy that defines them and the topics next to the manifest
that publishes them: rename either and a test fails, instead of an edge
going quietly dark.

The words for an edge are not here. The UI captions it, the way it
captions a region kind.

A grade is not decoration. It fixes how the edge is stroked and what its
tooltip may claim, so a path can never look more certain than the thing
watching it. A path with nothing watching it is still published — the
machine has that path either way — and says so in grey.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .taxonomy import Badge

# Privilege bands the UI stacks. An endpoint names one when the path
# belongs to a whole layer: every guest traps, and eight lines saying so
# hide the one thing they have in common.
BAND_EL1 = "band:el1"
BAND_PE = "band:pe"
BAND_DEV = "band:dev"
BANDS = (BAND_EL1, BAND_PE, BAND_DEV)

# EL2 is drawn as named chips rather than one block, so a path into the
# hypervisor can say which part of it.
CHIPS = ("trap", "sched", "timer", "vgic", "vuart", "ivc")
# Anchors on the address strip.
SEGMENTS = ("mem", "pa:shared")

GRADE_DIRECT = "direct"  # a record or a stop witnessed it — the moment itself
GRADE_CONSOLE = "console"  # a classified log line — exact in time
GRADE_POLL = "poll"  # a snapshot delta — quantised to the sample
GRADE_NONE = "none"  # structure, with nothing watching it

# Edge names other modules refer to. The event catalogue says which path
# a stop is evidence for, and a literal there would let a rename here
# leave it pointing at nothing.
EDGE_TRAP = "trap"
EDGE_POST = "post"
EDGE_INJECT = "inject"
EDGE_MMIO = "mmio"
EDGE_DMA = "dma"
EDGE_WALK = "walk"

# Expansion groups. A path between two instances of the same thing names
# the group, because how many there are is the machine's answer.
PAIR_CORES = "cores"


@dataclass(frozen=True)
class Edge:
    id: str
    source: str
    target: str
    grade: str
    topic: str = ""
    badges: tuple[Badge, ...] = ()
    pair: str = ""


EDGES: tuple[Edge, ...] = (
    Edge(EDGE_TRAP, BAND_EL1, "trap", GRADE_POLL, "ctx.syndrome", (Badge.TRAP,)),
    Edge("phys", "gicd", BAND_PE, GRADE_CONSOLE, badges=(Badge.IRQ, Badge.GIC)),
    # The hop between a device posting and the list register taking it,
    # read from the token store the post writes and the refill empties.
    Edge(EDGE_POST, "gicd", "vgic", GRADE_POLL, "vgic.token"),
    Edge(EDGE_INJECT, "vgic", BAND_EL1, GRADE_POLL, "vgic.lr"),
    Edge(EDGE_MMIO, BAND_EL1, "vgic", GRADE_CONSOLE, badges=(Badge.VGIC,)),
    Edge(EDGE_DMA, BAND_DEV, "smmu", GRADE_POLL, "dev.dma", (Badge.DMA,)),
    Edge(EDGE_WALK, "smmu", "mem", GRADE_CONSOLE, badges=(Badge.SMMU,)),
    Edge("cross", "", "", GRADE_POLL, "smp.mail", (Badge.SMP,), pair=PAIR_CORES),
    Edge("ivc", "ivc", "pa:shared", GRADE_POLL, "ivc.page"),
    Edge("psci", "sched", BAND_PE, GRADE_CONSOLE, badges=(Badge.PSCI,)),
    Edge("uart", "vuart", "uart0", GRADE_POLL, "dev.uart", (Badge.VUART, Badge.MUX)),
)


def _expand(edge: Edge, cpus: int) -> list[tuple[str, str]]:
    if edge.pair == PAIR_CORES:
        return [(f"core{cpu - 1}", f"core{cpu}") for cpu in range(1, cpus)]
    return [(edge.source, edge.target)]


def edges(cpus: int, blocks: Iterable[str], direct: Iterable[str] = ()) -> list[dict]:
    """Concrete paths for one board.

    Expanded and filtered here so the UI gets a flat list and resolves
    every endpoint by name. A board with no SMMU has fewer paths — a fact
    about the board, not a line drawn to nowhere.

    `direct` names the paths the machine can be stopped on. Those are
    upgraded, because a breakpoint is not another sample: it is the
    event itself, with the whole machine held still around it. The
    caller supplies the set rather than this module reading the event
    catalogue, which names paths from here — one direction only.
    """
    known = set(blocks) | set(BANDS) | set(CHIPS) | set(SEGMENTS)
    known |= {f"core{cpu}" for cpu in range(cpus)}
    observable = set(direct)
    out = []
    for edge in EDGES:
        for index, (source, target) in enumerate(_expand(edge, cpus)):
            if source not in known or target not in known:
                continue
            out.append({
                "id": edge.id if index == 0 else f"{edge.id}{index}",
                "from": source,
                "to": target,
                "grade": GRADE_DIRECT if edge.id in observable else edge.grade,
                "topic": edge.topic,
                "badges": [badge.value for badge in edge.badges],
            })
    return out
