#pragma once

// NovaVisor QEMU virt AArch64 Project Composition
//
// The single assembly point for the QEMU virt ARM64 target: it names
// the active components in cib::components<...>, and boot_order_
// component below declares the one RuntimeStart chain they run in.
// cib::top<nova_project> wires everything at compile time (BSS is
// already cleared by hal/arch/aarch64/boot/boot.S before any C++ runs;
// EarlyRuntimeInit has no actions).
//
// Boot order is a composition concern, not a peer-component one — a
// component cannot know which siblings a given project composes. Every
// RuntimeStart edge therefore lives here in one readable chain rather
// than scattered across the components that happen to need an
// ordering; the components only publish their own INIT action.
//
// Beyond RuntimeStart, MainLoop is claimed by core_vcpu_component
// (ERET to EL1, [[noreturn]]); the remaining components contribute
// only trap/IRQ service handlers, which need no ordering.

#include "boot_msg/boot_msg.hpp"
#include "command/command.hpp"
#include "core_gic/core_gic.hpp"
#include "core_mmu/core_mmu.hpp"
#include "core_timer/core_timer.hpp"
#include "core_vcpu/core_vcpu.hpp"
#include "demo_hvc/demo_hvc.hpp"
#include "dma_device/dma_device.hpp"
#include "dma_probe/dma_probe.hpp"
#include "ivc/ivc.hpp"
#include "psci/psci.hpp"
#include "smmu/smmu.hpp"
#include "smp/smp.hpp"
#include "soft_timer/soft_timer.hpp"
#include "telemetry/telemetry.hpp"
#include "trace/trace.hpp"
#include "trap_handler/trap_handler.hpp"
#include "vgic/vgic.hpp"
#include "vuart/vuart.hpp"
#include "watchdog/watchdog.hpp"

#include <cib/top.hpp>

namespace nova {

// The project's RuntimeStart order, as one linear chain. topo_sort
// imposes no order without edges, so every dependency in this profile
// is spelled out here:
//   - core_mmu first: Stage 2 must be live before anything maps.
//   - core_gic before smmu/vgic/core_timer/soft_timer/vuart: they all
//     program distributor or redistributor frames it wakes.
//   - core_timer before soft_timer: CNTHP must be disarmed first.
//   - vgic and core_vcpu before dma_device: it registers vIRQ backends.
//   - dma_device before dma_probe: the probe drives a configured device.
//   - dma_probe before boot_msg: the demo harness expects probe output
//     ahead of the banner.
//   - smp after the rest: a secondary touches shared state the instant
//     CPU_ON lands, so every other init must have completed.
//   - telemetry after everything it publishes: the first turn should
//     read a machine that is built, not one still building.
//   - command last of all: it publishes the page the host writes into,
//     and that is the moment something outside the machine may ask for
//     work — so everything a command can reach exists before it does.
//     After telemetry, so a host can see what a command did to a
//     machine it could already read.
struct boot_order_component {
  constexpr static auto config = cib::config(cib::extend<cib::RuntimeStart>(
      trace_component::INIT >> core_mmu_component::INIT >> core_gic_component::INIT >> smmu_component::INIT >>
      vgic_component::INIT >> core_timer_component::INIT >> soft_timer_component::INIT >> core_vcpu_component::INIT >>
      vuart_component::INIT >> dma_device_component::INIT >> dma_probe_component::INIT >>
      boot_msg_component::PRINT_BOOT_MSG >> smp_component::INIT >> telemetry_component::INIT >>
      command_component::INIT));
};

struct nova_project {
  constexpr static auto config =
      cib::components<trace_component, core_mmu_component, core_gic_component, vgic_component, core_timer_component,
                      soft_timer_component, boot_msg_component, trap_handler_component, demo_hvc_component,
                      ivc_component, psci_component, watchdog_component, smmu_component, dma_device_component,
                      dma_probe_component, smp_component, vuart_component, core_vcpu_component, telemetry_component,
                      command_component, boot_order_component>;
};

// nova_top is the concrete cib::top instantiation for this target.
// Calling nova_top{}.main() hands control to the CIB framework.
using nova_top = cib::top<nova_project>;

} // namespace nova
