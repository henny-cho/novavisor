"""The observation manifest: the S layer's single source of truth.

Every entry names a firmware global by its C++ qualified name, the wire
topic its decoded value feeds, and (optionally) which struct fields
travel. CI resolves every entry against the built debug ELF, so a
renamed symbol or a reshaped struct fails the pipeline instead of
silently blanking a panel.

The page table arrays below are read once per run rather than polled and
so feed no topic, but they are firmware globals named by hand like the
rest — declared here, they are resolved and CI-checked by the same code.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...core import config
from ...image import abi
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
    topic: str
    symbol: str
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


OBSERVATIONS: tuple[Obs, ...] = (
    # Scheduler panel
    Obs("sched.cpu", "nova::vcpu::g_sched", rate_hz=20, shape=derive.none_if_unset),
    Obs("sched.slots", "nova::vcpu::g_published_state", rate_hz=20),
    Obs("sched.run", "nova::vcpu::g_vcpus", fields=("state",), rate_hz=20),
    Obs("sched.affinity", "nova::vcpu::g_affinity", rate_hz=2),
    Obs("sched.valid", "nova::vcpu::g_slot_valid", rate_hz=2),
    Obs("sched.slice", "nova::vcpu::g_slice_ticks", rate_hz=10),
    # Timer panel
    Obs("timer.queue", "nova::soft_timer::(anonymous)::g_queue", fields=("deadline", "armed"),
        shape=derive.timer_armed),
    Obs("timer.programmed", "nova::soft_timer::(anonymous)::g_programmed",
        shape=derive.none_if_unset),
    Obs("timer.cntvoff", "nova::vcpu::g_cntvoff", rate_hz=2),
    Obs("vm.generation", "nova::vcpu::g_vm_generation", rate_hz=2),
    # Context panel — the whole file, twice a second.
    Obs("ctx.trap", "nova::vcpu::g_vcpus", fields=("ctx",), rate_hz=2, hex=True),
    # The board — the syndrome only, current. Five times the rate at a
    # fraction of the bytes, because it carries three words per slot
    # instead of forty.
    Obs("ctx.syndrome", "nova::vcpu::g_vcpus", fields=("ctx",), rate_hz=10,
        shape=derive.trap_syndrome),
    Obs("ctx.el1", "nova::vcpu::g_vcpus", fields=("el1",), rate_hz=2, hex=True),
    # PSCI / SMP panel
    Obs("smp.lifecycle", "nova::smp::g_lifecycle", rate_hz=5),
    Obs("smp.mode", "nova::smp::g_lifecycle_mode", rate_hz=5),
    Obs("smp.online", "nova::smp::g_online", rate_hz=2),
    Obs("smp.mail", "nova::smp::g_mail", fields=("count",), rate_hz=5),
    Obs("smp.budget", "nova::vcpu::g_budget", rate_hz=2),
    # vGIC panel — injection state, the only route to it. The gdb stub's
    # register set carries no ICH_*, so the EL2 shadow is all there is.
    Obs("vgic.lr", "nova::vgic::(anonymous)::g_cpu", fields=("lr", "lr_token"), rate_hz=10,
        shape=derive.vgic_inflight),
    # The hop before that one: posted by a device, not yet refilled into a
    # register. refill() moves the token rather than copying it, so this
    # list and the in-flight one are disjoint by construction — which is
    # what makes the position readable from a single snapshot.
    Obs("vgic.token", "nova::vgic::(anonymous)::g_spi_tokens", rate_hz=5,
        shape=derive.vgic_posted),
    Obs("vgic.dist", "nova::vgic::(anonymous)::g_dist",
        fields=("ctlr", "spi_group", "spi_enabled", "spi_pending"), rate_hz=5, hex=True),
    Obs("vgic.resident", "nova::vgic::(anonymous)::g_resident", rate_hz=5,
        shape=derive.none_if_unset),
    # Settled in EL2 init, after topo is published — hence polled, and
    # constant thereafter, so the change gate emits it once.
    Obs("vgic.capacity", "nova::vgic::(anonymous)::g_lr_count", rate_hz=2),
    # Devices panel
    Obs("dev.uart", "nova::vuart::(anonymous)::g_uart", fields=("head", "count", "imsc"), rate_hz=5),
    Obs("dev.dma", "nova::dma_device::(anonymous)::g_registry", rate_hz=5,
        shape=derive.none_if_unset),
    # What each device stream is allowed to do. Polled rather than read
    # once with the tables it points at: a fault quarantines a stream,
    # and the entry that changes is this one.
    Obs("smmu.stream", "nova::smmu::(anonymous)::g_stream_table", rate_hz=5,
        shape=derive.smmu_streams),
    Obs("dev.watchdog", "nova::(anonymous)::g_update_sequence", rate_hz=2),
    # IVC panel — the shared page is guest memory, not an EL2 global.
    Obs("ivc.page", "", pa=_BOARD["NOVA_BOARD_IVC_SHM_PA"], layout="ivc_ring_page", hex=True),
)


# Page table storage. Extents come from the DWARF, so a resized pool is
# copied whole without this list changing.
STAGE2_SETS = "nova::(anonymous)::g_stage2_sets"
DMA_TABLES = "nova::smmu::(anonymous)::g_dma_tables"
EL2_ROOT = "nova_el2_l1_root"
EL2_POOL = "(anonymous)::g_pool"
TABLES = (STAGE2_SETS, DMA_TABLES, EL2_ROOT, EL2_POOL)

# Where each walk starts, as the machine holds it: the register value the
# CPU is given, and the root the SMMU built its stream table from. Taken
# from the run's configuration instead, these would describe a machine
# that was intended rather than one that booted.
VTTBR = "nova::(anonymous)::g_vttbr"
DMA_CONTEXTS = "nova::smmu::(anonymous)::g_contexts"
DMA_CONTEXT_COUNT = "nova::smmu::(anonymous)::g_context_count"
ROOTS = (VTTBR, DMA_CONTEXTS, DMA_CONTEXT_COUNT)

WALK_SYMBOLS = TABLES + ROOTS


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
    "kSlotCount": "",  # the end marker, not a group
}


def _slot_bases() -> dict[str, int]:
    """Where each soft_timer slot group starts, read from the header.

    The bases are written there as sums over the ABI's own extents
    (`kSlotWatchdog = kSlotCntvWake + kMaxVcpus`), which the header gets
    from elsewhere and this supplies. Evaluating those sums reads the one
    definition; restating them would be a second one, and a group
    inserted between two others would silently shift every label after it
    by a slot.
    """
    known = abi.read_constexprs(SLOT_HEADER, {"kMaxVcpus": MAX_VCPUS, "kMaxGuests": MAX_GUESTS})
    missing = set(SLOT_NAMES) - set(known)
    if missing:
        raise SystemExit(f"nova workbench: no slot base for {sorted(missing)}")
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
