#pragma once

// Minimal GICv3 guest-hosting composition shared by board profiles.
// No SMP, no device passthrough, no vuart — a single core running the
// boot guest. Boot order lives in boot_order_component below, the same
// way the full profile declares it.
//
// No psci here, and that is a contract, not an omission: guest-facing
// PSCI power control routes through smp, which owns the DMA quiesce
// steps of VM power (dma_device + smmu). Pulling that whole stack into
// a single-core profile would drag in device isolation this profile
// deliberately excludes. The generated guest DT therefore omits its
// psci node for this composition (NOVA_PROJECT_SERVES_PSCI), so a
// guest never probes an interface nobody serves.

#include "boot_msg/boot_msg.hpp"
#include "core_gic/core_gic.hpp"
#include "core_mmu/core_mmu.hpp"
#include "core_timer/core_timer.hpp"
#include "core_vcpu/core_vcpu.hpp"
#include "soft_timer/soft_timer.hpp"
#include "telemetry/telemetry.hpp"
#include "trace/trace.hpp"
#include "trap_handler/trap_handler.hpp"
#include "vgic/vgic.hpp"

#include <cib/top.hpp>

namespace nova {

// RuntimeStart order for this profile: Stage 2 first, then the physical
// GIC before every component that programs a distributor or
// redistributor frame, core_timer before soft_timer (CNTHP disarmed
// first), telemetry once every component it publishes exists, and the
// banner once the guest is seeded.
struct boot_order_component {
  constexpr static auto config = cib::config(cib::extend<cib::RuntimeStart>(
      trace_component::INIT >> core_mmu_component::INIT >> core_gic_component::INIT >> vgic_component::INIT >>
      core_timer_component::INIT >> soft_timer_component::INIT >> core_vcpu_component::INIT >>
      telemetry_component::INIT >> boot_msg_component::PRINT_BOOT_MSG));
};

struct nova_project {
  constexpr static auto config =
      cib::components<trace_component, core_mmu_component, core_gic_component, vgic_component, core_timer_component,
                      soft_timer_component, boot_msg_component, trap_handler_component, core_vcpu_component,
                      telemetry_component, boot_order_component>;
};

using nova_top = cib::top<nova_project>;

} // namespace nova
