#pragma once

// components/smp/include/smp/smp.hpp
//
// Physical secondary-core bring-up.
//
// RuntimeStart (primary, ordered after every other init action —
// secondaries must observe fully initialized shared state): powers
// each secondary on through the firmware PSCI SMC conduit, targeting
// nova_secondary_entry (boot.S) with the core index as context id.
// The secondary sets up its per-core hardware (redistributor, CPU
// interface, EL2 timer) on its own stack, reports online, and parks.
//
// The conduit is SMC — firmware-facing — and entirely separate from
// the HVC PSCI the psci component emulates for guests.

#include "boot_msg/boot_msg.hpp"
#include "core_gic/core_gic.hpp"
#include "core_mmu/core_mmu.hpp"
#include "core_timer/core_timer.hpp"
#include "core_vcpu/core_vcpu.hpp"
#include "soft_timer/soft_timer.hpp"
#include "vgic/vgic.hpp"

#include <cib/top.hpp>
#include <flow/flow.hpp>

namespace nova::smp {

// Power on every secondary core and wait (bounded) for each to report
// online. A core that fails to start is logged and skipped — the
// system continues on the cores it has.
void start_secondaries() noexcept;

} // namespace nova::smp

namespace nova {

struct smp_component {
  constexpr static auto INIT = flow::action<"smp_start_secondaries">([]() noexcept { smp::start_secondaries(); });

  // Explicit flow edges: a secondary begins touching shared state
  // (GIC frames, VM table, timer queues) the moment CPU_ON lands, so
  // every other RuntimeStart action must have completed first. The
  // chain also pins the boot order the other inits relied on
  // implicitly (topo_sort gives no order without edges).
  constexpr static auto config = cib::config(cib::extend<cib::RuntimeStart>(
      core_mmu_component::INIT >> core_gic_component::INIT >> vgic_component::INIT >> core_timer_component::INIT >>
      soft_timer_component::INIT >> core_vcpu_component::INIT >> boot_msg_component::PRINT_BOOT_MSG >> *INIT));
};

} // namespace nova
