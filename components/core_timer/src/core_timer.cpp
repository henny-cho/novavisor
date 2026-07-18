// components/core_timer/src/core_timer.cpp
//
// HVC_TIMER_SET arms the EL2 physical timer; its expiry IRQ is turned
// into a virtual timer interrupt (vINTID 27) for the guest that armed
// it — CNTHP is a single hardware one-shot, so it has one owner at a
// time (per-VCPU virtual timer queues are a backlog item).

#include "components/core_timer/include/core_timer.hpp"

#include "components/core_vcpu/include/core_vcpu.hpp"
#include "hal/timer.hpp"
#include "nova/hvc_abi.h"
#include "nova/trap_context.hpp"

#include <cstddef>
#include <cstdint>

namespace nova {
namespace {

std::size_t g_owner = 0;     // VCPU that armed the running one-shot
bool        g_armed = false; // guards g_owner and rejects double-arming

} // namespace

void core_timer_component::handle_hvc(HvcCall* call) noexcept {
  if (call->func_id != NOVA_HVC_FN_TIMER_SET) {
    return; // not ours — leave unclaimed for other subscribers
  }
  call->handled = true;

  if (g_armed && g_owner != vcpu::current_index()) {
    call->ctx->x[0] = kSmcccNotSupported; // one-shot busy on behalf of another VCPU
    return;
  }

  g_owner = vcpu::current_index();
  g_armed = true;
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
  g_armed = false;
  // False only when the owner exited meanwhile — nobody to notify.
  (void)vcpu::post_virq(g_owner, hyp_timer::kGuestTimerVintid);
}

} // namespace nova
