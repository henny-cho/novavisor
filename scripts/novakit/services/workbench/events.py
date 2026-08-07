"""The event catalogue: where the machine can be stopped, and what each
stop means.

The S layer samples state, so a transition shorter than its interval is
not slow — it is absent. Measured: the interrupt bind this catalogue's
first entry names was never once caught by polling, not at 10 Hz and not
at 500 Hz, because its residency in state space is a few dozen cycles
inside one lock.

A breakpoint does not have that problem. It arrives *at* the event, and
the machine then holds still for as long as the reader wants — so the
whole machine, not a 24-byte record, is what a stop yields.

One catalogue, two consumers. A stop point and a trace hook are the same
fact about the firmware: "here is a moment worth naming". The trace ring
will emit from these functions; the halt layer breaks on them. Writing
them twice would let the two drift into disagreeing about what the
machine does.

`edge` ties an event to the path it lights on the board, so a hit is
evidence for that path rather than a line in a log. `args` names the
AAPCS64 argument registers in order (x0, x1, ...) — an empty name skips
a position that carries a pointer or something else not worth showing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import paths


@dataclass(frozen=True)
class Event:
    id: str
    symbol: str
    edge: str = ""
    args: tuple[str, ...] = field(default_factory=tuple)
    label: str = ""


# Ordered by the injection path they sit on: a physical interrupt is
# bound to a virtual one, refilled into a list register, and finally
# EoI'd. Stopping at each in turn walks that path a hop at a time.
EVENTS: tuple[Event, ...] = (
    Event(
        "vgic.bind",
        "nova::vgic::post_spi_tracked",
        paths.EDGE_POST,
        ("vm", "vintid", "pintid", "generation"),
        "물리 SPI를 가상 INTID에 결속",
    ),
    Event("vgic.spi", "nova::vgic::post_spi", paths.EDGE_POST, ("vm", "vintid"),
          "하이퍼바이저가 SPI 생성"),
    Event("vgic.private", "nova::vgic::post_private", paths.EDGE_POST, ("slot", "vintid"),
          "하이퍼바이저가 PPI/SGI 생성"),
    Event("vgic.inject", "nova::vgic::refill", paths.EDGE_INJECT, (),
          "대기 인터럽트를 리스트 레지스터로"),
    Event("vgic.eoi", "nova::vgic::(anonymous)::drain_eois", paths.EDGE_INJECT, ("slot",),
          "게스트가 인터럽트 완료"),
    Event("trap", "nova::trap_handler_component::handle_lower_sync", paths.EDGE_TRAP, (),
          "EL1에서 EL2로 동기 예외"),
    Event("mmio", "nova::trap::dispatch_data_abort", paths.EDGE_MMIO, (),
          "게스트 MMIO 접근 트랩"),
    Event("sched.switch", "nova::vcpu::(anonymous)::switch_to", "", ("", "next"),
          "vCPU 전환"),
)

BY_ID = {event.id: event for event in EVENTS}


def catalogue() -> list[dict]:
    """What the UI needs to offer the reader a choice of stop points.

    Addresses are deliberately absent: they change with every build, and
    the UI has no use for one. The bridge resolves them per run.
    """
    return [
        {"id": event.id, "edge": event.edge, "args": list(event.args), "label": event.label}
        for event in EVENTS
    ]
