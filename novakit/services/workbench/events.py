"""The event catalogue: where the machine can be stopped, and what each
stop means.

The S layer samples state, so a transition shorter than its interval is
absent rather than merely late: the interrupt bind this catalogue's
first entry names lives a few dozen cycles inside one lock and was never
caught by polling, at 10 Hz or at 500 Hz.

A breakpoint arrives *at* the event and the machine then holds still, so
a stop yields the whole machine rather than a 32-byte record.

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

from dataclasses import KW_ONLY, dataclass, field

from ...image import abi
from . import paths

# The firmware's numbering for the same moments. Read from the ABI
# header both sides compile against, so a renumbered event cannot mean
# one thing to the ring writer and another to this reader.
_CODES = abi.read_define_family(abi.TRACE_RING, "NOVA_TRACE_EV_")
# Codes the host writes into the same stream. A separate family, not a
# continuation: the list above is "what the firmware emits", and this
# one inside it would read as a hook nobody implemented.
_HOST_CODES = abi.read_define_family(abi.TRACE_RING, "NOVA_TRACE_HOST_EV_")


@dataclass(frozen=True)
class Event:
    id: str
    symbol: str
    edge: str = ""
    args: tuple[str, ...] = field(default_factory=tuple)
    label: str = ""
    # Everything past here is named at the call site. `args` and
    # `fields` are both tuples of strings sitting next to each other,
    # and a positional list is a swap nothing would catch: the
    # breakpoint would read the record's words and the mark would show
    # the registers, both plausibly.
    _: KW_ONLY
    # The record type the firmware writes for this moment. A stop point
    # and a trace hook are one fact, so they share an entry rather than
    # two tables free to disagree.
    code: int = 0
    # What the record's three argument words hold, in order. A separate
    # list from `args`, which names the AAPCS64 registers a breakpoint
    # reads: a stop sees the call, a record sees what was written down.
    # A packed word is named as the pair it is — splitting it belongs in
    # decode(), the one place that knows the packing.
    fields: tuple[str, str, str] = ("", "", "")
    # Whether the record describes a stretch of time rather than an
    # instant. Almost nothing does; a hole in the stream is the whole
    # exception, and drawn as a tick it would claim the axis around it
    # was watched.
    span: bool = False
    # Whether the record answers something the host asked for, rather
    # than reporting something the machine did. The control that issued
    # it needs it back, and knowing which entry that is belongs here
    # with every other per-event fact.
    reply: bool = False
    # Which of `fields` a run's totals are broken down by, when counting
    # the event as one kind is too coarse to act on: a thousand traps is
    # a number, a thousand traps that are all one EC is a cause. Named
    # rather than indexed, so the declaration cannot drift from the
    # layout beside it.
    group: str = ""

    def __post_init__(self) -> None:
        if not self.group:
            return
        if self.group not in self.fields:
            raise ValueError(
                f"{self.id}: group {self.group!r} is not one of its fields {self.fields}"
            )
        if "|" in self.group:
            raise ValueError(
                f"{self.id}: {self.group!r} is a packed pair, so grouping by it would "
                "count packings rather than values; unpacking belongs in decode()"
            )

    @property
    def group_index(self) -> int:
        """Which record word holds the breakdown, or -1 for none."""
        return self.fields.index(self.group) if self.group else -1

    @property
    def stop(self) -> bool:
        """Whether the machine can be halted here.

        Every firmware moment can. A record the host writes about the
        stream itself cannot — there is no instruction to break on —
        and the missing symbol is that fact rather than a hole in the
        table.
        """
        return bool(self.symbol)


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
        code=_CODES["NOVA_TRACE_EV_VGIC_BIND"],
        fields=("vm", "intids", "generation"),
    ),
    Event("vgic.spi", "nova::vgic::post_spi", paths.EDGE_POST, ("vm", "vintid"),
          "하이퍼바이저가 SPI 생성", code=_CODES["NOVA_TRACE_EV_VGIC_POST"],
          fields=("vm", "vintid", "")),
    Event("vgic.private", "nova::vgic::post_private", paths.EDGE_POST, ("slot", "vintid"),
          "하이퍼바이저가 PPI/SGI 생성", code=_CODES["NOVA_TRACE_EV_VGIC_PRIVATE"],
          fields=("slot", "vintid", "")),
    Event("vgic.inject", "nova::vgic::refill", paths.EDGE_INJECT, (),
          "대기 인터럽트를 리스트 레지스터로", code=_CODES["NOVA_TRACE_EV_VGIC_INJECT"],
          fields=("slot", "vintid|lr", "generation")),
    Event("vgic.eoi", "nova::vgic::(anonymous)::drain_eois", paths.EDGE_INJECT, ("slot",),
          "게스트가 인터럽트 완료", code=_CODES["NOVA_TRACE_EV_VGIC_EOI"],
          fields=("slot", "intids", "generation")),
    Event("trap", "nova::trap_handler_component::handle_lower_sync", paths.EDGE_TRAP, (),
          "EL1에서 EL2로 동기 예외", code=_CODES["NOVA_TRACE_EV_TRAP"],
          fields=("ec", "esr", "far"), group="ec"),
    Event("mmio", "nova::trap::dispatch_data_abort", paths.EDGE_MMIO, (),
          "게스트 MMIO 접근 트랩", code=_CODES["NOVA_TRACE_EV_MMIO"],
          fields=("access", "ipa", "value")),
    Event("sched.switch", "nova::vcpu::(anonymous)::switch_to", "", ("", "next"),
          "vCPU 전환", code=_CODES["NOVA_TRACE_EV_SCHED_SWITCH"],
          fields=("next", "prev", "")),
    # The moments that used to be read off console text or inferred from
    # a snapshot delta. Each sits on the normal path, not on an error
    # branch: an edge whose evidence only appears when something breaks
    # would claim certainty for the ordinary case it never watched.
    Event("gic.ack", "nova::core_gic::drain", "phys", ("intid",),
          "물리 IRQ를 EL2가 수신", code=_CODES["NOVA_TRACE_EV_GIC_ACK"],
          fields=("intid", "", ""), group="intid"),
    Event("smp.cross", "nova::smp::invoke_vm_owner", "cross", ("vm", "owner"),
          "다른 코어에 소유권 호출 전달", code=_CODES["NOVA_TRACE_EV_CROSS_CALL"],
          fields=("vm", "owner", "")),
    Event("ivc.doorbell", "nova::ivc_component::handle_hvc", "ivc", ("vm", "vintid"),
          "게스트가 IVC 초인종을 울림", code=_CODES["NOVA_TRACE_EV_IVC_DOORBELL"],
          fields=("vm", "vintid", "")),
    Event("psci.call", "nova::psci_component::handle_hvc", "psci", ("func", "arg"),
          "게스트 전원 제어 호출", code=_CODES["NOVA_TRACE_EV_PSCI"],
          fields=("func", "arg", "action")),
    Event("uart.line", "nova::console_mux::(anonymous)::emit", "uart", ("slot", "bytes"),
          "게스트 콘솔 한 줄 방출", code=_CODES["NOVA_TRACE_EV_UART_LINE"],
          fields=("slot", "bytes", "")),
    # No edge on purpose. A DMA fault is worth a lane of its own, and it
    # is not evidence about the path a working translation takes — see
    # the grade rule at the top of paths.py.
    Event("smmu.fault", "nova::smmu::(anonymous)::dispatch_faults", "", ("stream", "vm"),
          "SMMU 변환 폴트", code=_CODES["NOVA_TRACE_EV_SMMU_FAULT"],
          fields=("stream", "vm", "generation")),
    # The two hops the board drew with nothing watching them. A device's
    # transaction leaving for the SMMU is a moment the firmware has; the
    # walk that follows it is the SMMU's own, in hardware, with no
    # instruction to break on — so what is witnessed there is the route
    # being established, which is what the edge is about.
    Event("dma.start", "nova::dma_device::start_dma", paths.EDGE_DMA,
          ("", "vm", "generation", "source"),
          "장치가 전송을 시작", code=_CODES["NOVA_TRACE_EV_DMA_START"],
          fields=("vm", "address", "bytes")),
    Event("smmu.attach", "nova::smmu::(anonymous)::install_stream", paths.EDGE_WALK,
          ("stream",),
          "스트림을 VM의 Stage 2 테이블에 결속", code=_CODES["NOVA_TRACE_EV_SMMU_ATTACH"],
          fields=("stream", "root", "vmid")),
    # EL2 acknowledges a command by emitting this and nothing else,
    # which puts an instruction and its consequences on one axis in one
    # clock and makes a refusal as visible as an acceptance.
    Event("command", "nova::command::execute", "", (),
          "호스트 명령을 EL2가 실행", code=_CODES["NOVA_TRACE_EV_COMMAND"],
          fields=("op|result", "a", "b"), reply=True),
    # Not a moment in the firmware but a statement about the stream:
    # written by the reader where the records it could not recover
    # would have been. No symbol, so it is never offered as a stop
    # point; no edge, because the grade rule in paths.py says exactly
    # this — a stretch nothing was watching is evidence for no path.
    Event("trace.gap", "", "", label="관측되지 않은 구간",
          code=_HOST_CODES["NOVA_TRACE_HOST_EV_GAP"],
          fields=("count", "from", ""), span=True),
)

# Where the machine can actually be stopped. The catalogue is one list
# because a stop point and a trace hook are one fact, and the entries
# that are only a record kind are the exception this names once.
STOPS: tuple[Event, ...] = tuple(event for event in EVENTS if event.stop)

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
        {
            "id": event.id,
            "edge": event.edge,
            "args": list(event.args),
            "label": event.label,
            # Column-encoded records carry the firmware's number, so the
            # UI needs the same mapping the ring writer used.
            "code": event.code,
            # What the record's three words hold, for a reader who
            # clicks a mark. Named here so the UI never learns a layout
            # the bridge already knows.
            "fields": list(event.fields),
            # Whether this entry can be armed, and whether its records
            # cover a stretch rather than an instant. The UI builds a
            # picker and a lane from one list, and these are what let it
            # do both without a copy of the catalogue's exceptions.
            "stop": event.stop,
            "span": event.span,
        }
        for event in EVENTS
    ]
