// components/core_timer/src/core_timer.cpp
//
// HVC_TIMER_SET arms a soft_timer slot; its expiry is turned into a
// virtual timer interrupt (vINTID 27) for the guest that armed it —
// the legacy slot is single, so it has one owner at a time (per-VCPU
// virtual timer queues are a backlog item).

#include "core_timer/core_timer.hpp"

#include "core_vcpu/core_vcpu.hpp"
#include "hal/timer.hpp"
#include "nova/abi/hvc_abi.h"
#include "nova/arch/trap_context.hpp"
#include "soft_timer/soft_timer.hpp"

#include <cstddef>
#include <cstdint>

namespace nova {
namespace {

std::size_t g_owner = 0;     // VCPU that armed the running one-shot
bool        g_armed = false; // guards g_owner and rejects double-arming

// Legacy-slot expiry (runs in soft_timer's IRQ drain). False from
// post_virq only when the owner exited meanwhile — nobody to notify.
void on_timer_set_expiry(TrapContext* /*ctx*/, std::uint64_t owner) noexcept {
  g_armed = false;
  (void)vcpu::post_virq(static_cast<std::size_t>(owner), hyp_timer::kGuestTimerVintid);
}

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
  soft_timer::arm(soft_timer::kSlotLegacyTimer, hyp_timer::now() + call->ctx->x[1], &on_timer_set_expiry,
                  static_cast<std::uint64_t>(g_owner));
  call->ctx->x[0] = 0; // SMCCC success
}

void core_timer_component::handle_irq(IrqCall* call) noexcept {
  if (call->intid != hyp_timer::kGuestTimerVintid) {
    return; // not ours (CNTHP belongs to soft_timer)
  }
  call->handled = true;

  // Native guest CNTV expiry. The firing timer is by construction the
  // resident VCPU's (a non-resident VCPU's CNTV state sits parked in
  // its EL1 bank and cannot meet the condition). Mask the level
  // assertion, then reflect the PPI as its virtual counterpart; the
  // guest clears IMASK when it re-arms CNTV_CTL.
  hyp_timer::mask_guest_virtual_timer();
  (void)vcpu::post_virq(vcpu::current_index(), hyp_timer::kGuestTimerVintid);
}

} // namespace nova
