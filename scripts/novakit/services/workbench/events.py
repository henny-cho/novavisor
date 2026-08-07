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

from ...image import abi
from . import paths

# The firmware's numbering for the same moments. Read from the ABI
# header both sides compile against, so a renumbered event cannot mean
# one thing to the ring writer and another to this reader.
_CODES = abi.read_defines(
    abi.TRACE_RING,
    [
        "NOVA_TRACE_EV_TRAP",
        "NOVA_TRACE_EV_VGIC_BIND",
        "NOVA_TRACE_EV_VGIC_POST",
        "NOVA_TRACE_EV_VGIC_PRIVATE",
        "NOVA_TRACE_EV_VGIC_INJECT",
        "NOVA_TRACE_EV_VGIC_EOI",
        "NOVA_TRACE_EV_SCHED_SWITCH",
        "NOVA_TRACE_EV_MMIO",
        "NOVA_TRACE_EV_GIC_ACK",
        "NOVA_TRACE_EV_CROSS_CALL",
        "NOVA_TRACE_EV_IVC_DOORBELL",
        "NOVA_TRACE_EV_PSCI",
        "NOVA_TRACE_EV_UART_LINE",
        "NOVA_TRACE_EV_SMMU_FAULT",
    ],
)


@dataclass(frozen=True)
class Event:
    id: str
    symbol: str
    edge: str = ""
    args: tuple[str, ...] = field(default_factory=tuple)
    label: str = ""
    # The record type the firmware writes for this moment. A stop point
    # and a trace hook are one fact, so they share an entry rather than
    # two tables free to disagree.
    code: int = 0


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
        _CODES["NOVA_TRACE_EV_VGIC_BIND"],
    ),
    Event("vgic.spi", "nova::vgic::post_spi", paths.EDGE_POST, ("vm", "vintid"),
          "하이퍼바이저가 SPI 생성", _CODES["NOVA_TRACE_EV_VGIC_POST"]),
    Event("vgic.private", "nova::vgic::post_private", paths.EDGE_POST, ("slot", "vintid"),
          "하이퍼바이저가 PPI/SGI 생성", _CODES["NOVA_TRACE_EV_VGIC_PRIVATE"]),
    Event("vgic.inject", "nova::vgic::refill", paths.EDGE_INJECT, (),
          "대기 인터럽트를 리스트 레지스터로", _CODES["NOVA_TRACE_EV_VGIC_INJECT"]),
    Event("vgic.eoi", "nova::vgic::(anonymous)::drain_eois", paths.EDGE_INJECT, ("slot",),
          "게스트가 인터럽트 완료", _CODES["NOVA_TRACE_EV_VGIC_EOI"]),
    Event("trap", "nova::trap_handler_component::handle_lower_sync", paths.EDGE_TRAP, (),
          "EL1에서 EL2로 동기 예외", _CODES["NOVA_TRACE_EV_TRAP"]),
    Event("mmio", "nova::trap::dispatch_data_abort", paths.EDGE_MMIO, (),
          "게스트 MMIO 접근 트랩", _CODES["NOVA_TRACE_EV_MMIO"]),
    Event("sched.switch", "nova::vcpu::(anonymous)::switch_to", "", ("", "next"),
          "vCPU 전환", _CODES["NOVA_TRACE_EV_SCHED_SWITCH"]),
    # The moments that used to be read off console text or inferred from
    # a snapshot delta. Each sits on the normal path, not on an error
    # branch: an edge whose evidence only appears when something breaks
    # would claim certainty for the ordinary case it never watched.
    Event("gic.ack", "nova::core_gic::drain", "phys", ("intid",),
          "물리 IRQ를 EL2가 수신", _CODES["NOVA_TRACE_EV_GIC_ACK"]),
    Event("smp.cross", "nova::smp::invoke_vm_owner", "cross", ("vm", "owner"),
          "다른 코어에 소유권 호출 전달", _CODES["NOVA_TRACE_EV_CROSS_CALL"]),
    Event("ivc.doorbell", "nova::ivc_component::handle_hvc", "ivc", ("vm", "vintid"),
          "게스트가 IVC 초인종을 울림", _CODES["NOVA_TRACE_EV_IVC_DOORBELL"]),
    Event("psci.call", "nova::psci_component::handle_hvc", "psci", ("func", "arg"),
          "게스트 전원 제어 호출", _CODES["NOVA_TRACE_EV_PSCI"]),
    Event("uart.line", "nova::console_mux::(anonymous)::emit", "uart", ("slot", "bytes"),
          "게스트 콘솔 한 줄 방출", _CODES["NOVA_TRACE_EV_UART_LINE"]),
    # No edge on purpose. A DMA fault is worth a lane of its own, and it
    # is not evidence about the path a working translation takes — see
    # the grade rule at the top of paths.py.
    Event("smmu.fault", "nova::smmu::(anonymous)::dispatch_faults", "", ("stream", "vm"),
          "SMMU 변환 폴트", _CODES["NOVA_TRACE_EV_SMMU_FAULT"]),
)

BY_ID = {event.id: event for event in EVENTS}
BY_CODE = {event.code: event for event in EVENTS if event.code}


def observable(symbols, tracing: bool) -> set[str]:
    """Paths this run can produce direct evidence for.

    A different question from "which paths does the catalogue name".
    The catalogue is a source constant: it says the same thing about a
    stripped image, a build without the trace component, and a machine
    with no debug stub — which is exactly the overstatement grades exist
    to prevent, and it was the grade calculation itself making it.

    Two ways to witness a moment, so two tests. The rings record it
    wherever they are placed, symbols or not; a breakpoint needs the
    symbol but nothing else. `symbols` is None when the image has not
    been read yet, which rules nothing in.
    """
    out = set()
    for event in EVENTS:
        if not event.edge:
            continue
        if tracing and event.code:
            out.add(event.edge)
        elif symbols is not None and symbols.has_function(event.symbol):
            out.add(event.edge)
    return out


def catalogue() -> list[dict]:
    """What the UI needs to offer the reader a choice of stop points.

    Addresses are deliberately absent: they change with every build, and
    the UI has no use for one. The bridge resolves them per run.
    """
    return [
        {"id": event.id, "edge": event.edge, "args": list(event.args), "label": event.label}
        for event in EVENTS
    ]
