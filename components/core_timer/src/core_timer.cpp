// components/core_timer/src/core_timer.cpp
//
// HVC_TIMER_SET arms the EL2 physical timer; its expiry IRQ is turned
// into a virtual timer interrupt (vINTID 27) for the guest.

#include "components/core_timer/include/core_timer.hpp"

#include "hal/gic.hpp"
#include "hal/timer.hpp"
#include "nova/hvc_abi.h"
#include "nova/trap_context.hpp"

#include <cstdint>

namespace nova {

void core_timer_component::handle_hvc(HvcCall* call) noexcept {
  if (call->func_id != NOVA_HVC_FN_TIMER_SET) {
    return; // not ours — leave unclaimed for other subscribers
  }
  call->handled = true;

  hyp_timer::arm(call->ctx->x[1]);
  call->ctx->x[0] = 0; // SMCCC success
}

void core_timer_component::handle_irq(IrqCall* call) noexcept {
  if (call->intid != hyp_timer::kHypTimerIntid) {
    return;
  }
  call->handled = true;

  // One-shot: disarm before injecting, or CNTHP keeps its condition met
  // and re-fires forever.
  hyp_timer::stop();
  gic::inject_virq(hyp_timer::kGuestTimerVintid);
}

} // namespace nova
