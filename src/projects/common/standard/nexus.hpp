#pragma once

// Standard guest-hosting composition shared by board profiles: physical
// SMP, guest-facing PSCI, VM lifecycle and watchdog recovery, inter-VM
// doorbells and an emulated UART — everything a board needs to host
// unmodified guests, and nothing that requires a real IOMMU.
//
// This is the middle tier between minimal (single core, one guest, no
// device models) and the full profile (this plus SMMU-backed device
// passthrough). It exists because real-board bring-up needs SMP,
// lifecycle and console before an IOMMU is characterized: putting VM
// power and device isolation in one tier would mean a board could not
// demonstrate warm reset until its SMMU was up, mixing porting failures
// with device failures.
//
// What makes the split possible is DmaQuiesceService (smp/dma_quiesce.hpp):
// VM power publishes its drain/attach steps and the device stack
// subscribes, so an unsubscribed composition simply has no DMA to
// isolate. The generated guest DT follows the component list, so a guest
// here is promised psci and gets it.

#include "boot_msg/boot_msg.hpp"
#include "core_gic/core_gic.hpp"
#include "core_mmu/core_mmu.hpp"
#include "core_timer/core_timer.hpp"
#include "core_vcpu/core_vcpu.hpp"
#include "demo_hvc/demo_hvc.hpp"
#include "ivc/ivc.hpp"
#include "psci/psci.hpp"
#include "smp/smp.hpp"
#include "soft_timer/soft_timer.hpp"
#include "trace/trace.hpp"
#include "trap_handler/trap_handler.hpp"
#include "vgic/vgic.hpp"
#include "vuart/vuart.hpp"
#include "watchdog/watchdog.hpp"

#include <cib/top.hpp>

namespace nova {

// The full profile's chain with the device stack removed; every other
// edge is unchanged and carries the same reason:
//   - core_mmu first: Stage 2 must be live before anything maps.
//   - core_gic before vgic/core_timer/soft_timer/vuart: they all program
//     distributor or redistributor frames it wakes.
//   - core_timer before soft_timer: CNTHP must be disarmed first.
//   - boot_msg once the guest table is seeded.
//   - smp last: a secondary touches shared state the instant CPU_ON
//     lands, so every other init must have completed.
struct boot_order_component {
  constexpr static auto config = cib::config(cib::extend<cib::RuntimeStart>(
      trace_component::INIT >> core_mmu_component::INIT >> core_gic_component::INIT >> vgic_component::INIT >>
      core_timer_component::INIT >> soft_timer_component::INIT >> core_vcpu_component::INIT >> vuart_component::INIT >>
      boot_msg_component::PRINT_BOOT_MSG >> smp_component::INIT));
};

struct nova_project {
  constexpr static auto config =
      cib::components<trace_component, core_mmu_component, core_gic_component, vgic_component, core_timer_component,
                      soft_timer_component, boot_msg_component, trap_handler_component, demo_hvc_component,
                      ivc_component, psci_component, watchdog_component, smp_component, vuart_component,
                      core_vcpu_component, boot_order_component>;
};

using nova_top = cib::top<nova_project>;

} // namespace nova
