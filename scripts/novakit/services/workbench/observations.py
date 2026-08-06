"""The observation manifest: the S layer's single source of truth.

Every entry names a firmware global by its C++ qualified name, the wire
topic its decoded value feeds, and (optionally) which struct fields
travel. CI resolves every entry against the built debug ELF, so a
renamed symbol or a reshaped struct fails the pipeline instead of
silently blanking a panel.
"""

from __future__ import annotations

from dataclasses import dataclass

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
        shape=derive.none_if_unset),
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
    Obs("vgic.lr", "nova::vgic::(anonymous)::g_cpu", fields=("lr",), rate_hz=10,
        shape=derive.vgic_inflight),
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
    Obs("dev.watchdog", "nova::(anonymous)::g_update_sequence", rate_hz=2),
    # IVC panel — the shared page is guest memory, not an EL2 global.
    Obs("ivc.page", "", pa=_BOARD["NOVA_BOARD_IVC_SHM_PA"], layout="ivc_ring_page", hex=True),
)


def observation_rates() -> dict[str, float]:
    """How often each topic is sampled, for the UI to say so.

    A screen showing a sampled value has to be able to state how coarse
    the sample is, and the manifest is the only place that knows. Written
    into the UI instead, the two drift and the badge lies.
    """
    return {obs.topic: obs.rate_hz for obs in OBSERVATIONS}


def timer_slot_labels() -> list[str]:
    """Owner of each soft_timer slot, by the index convention the
    firmware allocates (soft_timer.hpp): slice, legacy, then per-vCPU
    wake and per-VM watchdog/lifecycle/dma-drain ranges."""
    labels = ["slice", "legacy"]
    labels += [f"cntv_wake v{slot}" for slot in range(MAX_VCPUS)]
    labels += [f"watchdog vm{vm}" for vm in range(MAX_GUESTS)]
    labels += [f"lifecycle vm{vm}" for vm in range(MAX_GUESTS)]
    labels += [f"dma_drain vm{vm}" for vm in range(MAX_GUESTS)]
    return labels
