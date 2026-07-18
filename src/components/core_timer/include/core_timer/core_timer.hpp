#pragma once

// components/core_timer/include/core_timer/core_timer.hpp
//
// Guest timer service.
//
// RuntimeStart: programs CNTVOFF_EL2 = 0 (guests see the physical
// counter through CNTV), disarms the EL2 physical timer, and enables
// the CNTV PPI at the GIC (the CNTHP PPI belongs to soft_timer).
//
// Native path: guests program CNTV_CTL/CVAL directly (never trapped at
// EL1). The physical CNTV PPI lands in EL2, which masks the
// level-triggered timer and posts vINTID 27 to the resident VCPU — the
// only one whose CNTV registers are live. The guest unmasks by
// re-arming.
//
// Legacy path (Phase 6 demos): HVC_TIMER_SET (0x1200, x1 = ticks)
// arms a soft_timer slot; its expiry is also delivered as vINTID 27,
// to the arming VCPU.

#include "core_gic/core_gic.hpp"
#include "hal/gic.hpp"
#include "hal/timer.hpp"
#include "trap_handler/hvc.hpp"

#include <cib/top.hpp>
#include <flow/flow.hpp>

namespace nova {

struct core_timer_component {
  static void handle_hvc(HvcCall* call) noexcept;
  static void handle_irq(IrqCall* call) noexcept;

  constexpr static auto INIT = flow::action<"core_timer_init">([]() noexcept {
    hyp_timer::init();
    gic::enable_ppi(hyp_timer::kGuestTimerVintid); // physical CNTV PPI (native guest timers)
  });

  // INIT touches the redistributor that core_gic_component::INIT wakes:
  // list core_timer after core_gic in the project's cib::components<> so
  // the RuntimeStart flow keeps that order. (An explicit `>>` edge is
  // not expressible here — GCC rejects cross-TU lambda action pointers
  // as template arguments.)
  constexpr static auto config =
      cib::config(cib::extend<cib::RuntimeStart>(*INIT), cib::extend<HvcService>(&core_timer_component::handle_hvc),
                  cib::extend<IrqService>(&core_timer_component::handle_irq));
};

} // namespace nova
