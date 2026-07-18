// components/core_timer/src/core_timer.cpp
//
// HVC_TIMER_SET arms a soft_timer slot; its expiry is turned into a
// virtual timer interrupt (vINTID 27) for the guest that armed it —
// the legacy slot is single, so it has one owner at a time (per-VCPU
// virtual timer queues are a backlog item).

#include "core_timer/core_timer.hpp"

#include "core_vcpu/core_vcpu.hpp"
#include "hal/cpu.hpp"
#include "hal/timer.hpp"
#include "nova/abi/hvc_abi.h"
#include "nova/arch/trap_context.hpp"
#include "soft_timer/soft_timer.hpp"

#include <array>
#include <cstddef>
#include <cstdint>

namespace nova {
namespace {

// One legacy one-shot per core (the slot itself lives in the per-core
// soft_timer queue): owner/armed pair guards double-arming. Arming and
// expiry both happen on the owner's core.
struct LegacySlot {
  std::size_t owner = 0;     // VCPU that armed the running one-shot
  bool        armed = false; // guards owner and rejects double-arming
};

std::array<LegacySlot, cpu::kMaxCpus> g_legacy;

// Legacy-slot expiry (runs in soft_timer's IRQ drain). False from
// post_virq only when the owner exited meanwhile — nobody to notify.
void on_timer_set_expiry(TrapContext* /*ctx*/, std::uint64_t owner) noexcept {
  g_legacy[cpu::id()].armed = false;
  (void)vcpu::post_virq(static_cast<std::size_t>(owner), hyp_timer::kGuestTimerVintid);
}

} // namespace

void core_timer_component::handle_hvc(HvcCall* call) noexcept {
  if (call->func_id != NOVA_HVC_FN_TIMER_SET) {
    return; // not ours — leave unclaimed for other subscribers
  }
  call->handled = true;

  LegacySlot& slot = g_legacy[cpu::id()];
  if (slot.armed && slot.owner != vcpu::current_index()) {
    call->ctx->x[0] = kSmcccNotSupported; // one-shot busy on behalf of another VCPU
    return;
  }

  slot.owner = vcpu::current_index();
  slot.armed = true;
  soft_timer::arm(soft_timer::kSlotLegacyTimer, hyp_timer::now() + call->ctx->x[1], &on_timer_set_expiry,
                  static_cast<std::uint64_t>(slot.owner));
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
