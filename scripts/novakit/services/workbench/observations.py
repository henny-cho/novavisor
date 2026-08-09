"""How each observation is sampled and drawn.

The other half — which global feeds which topic, and which members
travel — is the build's, since only an image can answer it. This half is
a rate, a shape and a spelling, and nothing in it can be checked against
an ELF.

The two meet at the topic, checked both ways: a policy for a topic
nobody resolves samples nothing, and a resolved symbol with no policy
goes out at a rate nobody chose.

The one exception carries no symbol: a page of guest memory has no
DWARF, so its address and layout are declared here and held to the board
map by the manifest check.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...core import config
from ...image import abi, observe
from . import derive, hardware

# Board facts the labels below derive from, read from the headers that
# define them; the manifest check then asserts the derived extents
# against the DWARF, so a rebuilt firmware cannot drift from either.
_BOARD = hardware.platform()
MAX_CPUS = _BOARD["NOVA_BOARD_SMP_CPUS"]
MAX_GUESTS = abi.MAX_GUESTS
# vCPU slots are flat: every guest owns a fixed stride of them.
MAX_VCPUS = abi.MAX_GUESTS * abi.MAX_VCPUS_PER_VM


@dataclass(frozen=True)
class Obs:
    """One observation, whole: what it reads and how it travels."""

    topic: str
    symbol: str = ""
    fields: tuple[str, ...] = ()
    rate_hz: float = 10.0
    # Register-like values travel as hex strings: JSON numbers lose
    # exactness past 2^53 and these are bit patterns anyway.
    hex: bool = False
    # A fixed physical address with a hand-declared layout replaces the
    # symbol for state that lives in guest memory (the IVC page).
    pa: int | None = None
    layout: str = ""
    # Turns a firmware encoding into what it means, before the wire.
    shape: derive.Shape | None = None


@dataclass(frozen=True)
class Policy:
    """How often a topic is sampled, and what it looks like on the wire."""

    rate_hz: float = 10.0
    hex: bool = False
    shape: derive.Shape | None = None


# How often the firmware takes a reading. Sampling faster asks the same
# slot twice for one answer, so this is the ceiling every entry below is
# checked against — read from the component that arms the turn rather
# than restated here.
PUBLISH_HZ = 1_000_000 / abi.read_constexprs(abi.TELEMETRY_COMPONENT, wanted={"kPeriodUs"})["kPeriodUs"]


POLICY: dict[str, Policy] = {
    # Scheduler panel
    "sched.cpu": Policy(rate_hz=20, shape=derive.none_if_unset),
    "sched.slots": Policy(rate_hz=20),
    "sched.run": Policy(rate_hz=20),
    "sched.affinity": Policy(rate_hz=2),
    "sched.valid": Policy(rate_hz=2),
    "sched.slice": Policy(rate_hz=10),
    # Timer panel
    "timer.queue": Policy(shape=derive.timer_armed),
    "timer.programmed": Policy(shape=derive.none_if_unset),
    "timer.cntvoff": Policy(rate_hz=2),
    "vm.generation": Policy(rate_hz=2),
    # Context panel — the whole trap frame, twice a second.
    "ctx.trap": Policy(rate_hz=2, hex=True),
    # The board — the syndrome only, current. Five times the rate at a
    # fraction of the bytes, because it carries three words per slot
    # instead of forty.
    "ctx.syndrome": Policy(rate_hz=10, shape=derive.trap_syndrome),
    "ctx.el1": Policy(rate_hz=2, hex=True),
    # Built once in EL2 init, so the change gate emits it once and the
    # rate only decides how soon.
    "vm.table": Policy(rate_hz=2, shape=derive.guest_table),
    # PSCI / SMP panel
    "smp.lifecycle": Policy(rate_hz=5),
    "smp.mode": Policy(rate_hz=5),
    "smp.online": Policy(rate_hz=2),
    "smp.mail": Policy(rate_hz=5),
    "smp.budget": Policy(rate_hz=2),
    # vGIC panel
    "vgic.lr": Policy(rate_hz=10, shape=derive.vgic_inflight),
    "vgic.token": Policy(rate_hz=5, shape=derive.vgic_posted),
    "vgic.dist": Policy(rate_hz=5, hex=True),
    "vgic.resident": Policy(rate_hz=5, shape=derive.none_if_unset),
    # Settled in EL2 init, after topo is published — hence polled, and
    # constant thereafter, so the change gate emits it once.
    "vgic.capacity": Policy(rate_hz=2),
    # Devices panel
    "dev.uart": Policy(rate_hz=5),
    "dev.dma": Policy(rate_hz=5, shape=derive.none_if_unset),
    # Polled rather than read once with the tables it points at: a fault
    # quarantines a stream, and the entry that changes is this one. At
    # the firmware's own rate because a stream's transit through
    # `translate` is short enough that 5 Hz never caught it, and an
    # unmoving table now costs a word.
    "smmu.stream": Policy(rate_hz=PUBLISH_HZ, shape=derive.smmu_streams),
    "dev.watchdog": Policy(rate_hz=2),
}

# IVC panel — the shared page is guest memory, not an EL2 global.
PAGES: tuple[Obs, ...] = (
    Obs("ivc.page", pa=_BOARD["NOVA_BOARD_IVC_SHM_PA"], layout="ivc_ring_page", hex=True),
)


def _joined() -> tuple[Obs, ...]:
    """The question and the policy, met at the topic.

    Either alone is a half-observation, and neither raises on its own.
    """
    asked = {want.topic for want in observe.OBSERVED}
    if asked != set(POLICY):
        raise SystemExit(
            "nova workbench: observed symbols and their policies disagree: "
            f"{sorted(asked ^ set(POLICY))}"
        )
    return (
        tuple(
            Obs(
                want.topic,
                want.symbol,
                want.fields,
                rate_hz=POLICY[want.topic].rate_hz,
                hex=POLICY[want.topic].hex,
                shape=POLICY[want.topic].shape,
            )
            for want in observe.OBSERVED
        )
        + PAGES
    )


OBSERVATIONS: tuple[Obs, ...] = _joined()


def _check_rates() -> None:
    """No entry may be sampled faster than the firmware publishes.

    Checked rather than clamped. A clamp would make a typo behave like a
    considered number; this names the entry and the ceiling it passed,
    which is the thing to look at.
    """
    too_fast = {obs.topic: obs.rate_hz for obs in OBSERVATIONS if obs.rate_hz > PUBLISH_HZ}
    if too_fast:
        raise SystemExit(
            f"nova workbench: sampled faster than the firmware publishes ({PUBLISH_HZ:g} Hz): {too_fast}"
        )


_check_rates()


# The reading the topology defers to. Named here with the rest of the
# manifest; the session is what does something with it.
GUEST_TABLE = "vm.table"

# The page the host writes commands into. Named here rather than beside
# the writer, so a rename fails the manifest check instead of a control
# that quietly stops working. Its extent comes from the symbol table.
COMMAND_PAGE = "nova::command::g_page"


def observation_rates() -> dict[str, float]:
    """How often each topic is sampled, for the UI to say so.

    A screen showing a sampled value has to be able to state how coarse
    the sample is, and the manifest is the only place that knows. Written
    into the UI instead, the two drift and the badge lies.
    """
    return {obs.topic: obs.rate_hz for obs in OBSERVATIONS}


SLOT_HEADER = (
    config.REPO
    / "src"
    / "components"
    / "service"
    / "soft_timer"
    / "include"
    / "soft_timer"
    / "soft_timer.hpp"
)
# What each group's entries are called. Only the words are here: where a
# group starts and how wide it is both come from the header.
SLOT_NAMES = {
    "kSlotSlice": "slice",
    "kSlotLegacyTimer": "legacy",
    "kSlotCntvWake": "cntv_wake v{}",
    "kSlotWatchdog": "watchdog vm{}",
    "kSlotLifecycle": "lifecycle vm{}",
    "kSlotDmaDrain": "dma_drain vm{}",
    "kSlotCommand": "command",
    "kSlotTelemetry": "telemetry",
    "kSlotCount": "",  # the end marker, not a group
}


def _slot_bases() -> dict[str, int]:
    """Where each soft_timer slot group starts, read from the header.

    The bases are sums over the ABI's own extents
    (`kSlotWatchdog = kSlotCntvWake + kMaxVcpus`), whose terms the header
    gets from elsewhere and this supplies. Evaluating them reads the one
    definition; restating them would let a group inserted between two
    others shift every label after it by a slot.

    Both directions are checked. A name here with no base is an obvious
    failure, but a base with no name is the quiet one: a group added to
    the header and not named would be absorbed into the width of the one
    before it, and every slot of the new group would go out labelled as
    its predecessor.
    """
    known = abi.read_constexprs(SLOT_HEADER, {"kMaxVcpus": MAX_VCPUS, "kMaxGuests": MAX_GUESTS})
    groups = {name for name in known if name.startswith("kSlot")}
    if groups != set(SLOT_NAMES):
        raise SystemExit(
            f"nova workbench: soft_timer slot groups and their names disagree: "
            f"{sorted(groups ^ set(SLOT_NAMES))}"
        )
    return {name: known[name] for name in SLOT_NAMES}


def timer_slot_labels() -> list[str]:
    """Owner of each soft_timer slot.

    A group is as wide as the gap to the next one, so nothing here
    repeats the firmware's arithmetic — reorder the groups and the
    labels follow.
    """
    ordered = sorted(_slot_bases().items(), key=lambda entry: entry[1])
    labels: list[str] = []
    for (name, base), (_, end) in zip(ordered, ordered[1:], strict=False):
        if base != len(labels):
            raise SystemExit(f"nova workbench: soft_timer slot groups overlap at {name}")
        template = SLOT_NAMES[name]
        width = end - base
        labels += [template.format(index) for index in range(width)] if width > 1 else [template]
    return labels
