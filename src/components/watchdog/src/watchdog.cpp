// components/watchdog/src/watchdog.cpp

#include "watchdog/watchdog.hpp"

#include "core_vcpu/core_vcpu.hpp"
#include "hal/console.hpp"
#include "hal/timer.hpp"
#include "nova/abi/guest.hpp"
#include "nova/abi/hvc_abi.h"
#include "nova/arch/trap_context.hpp"
#include "smp/smp.hpp"
#include "soft_timer/soft_timer.hpp"

#include <cstddef>
#include <cstdint>

namespace nova {
namespace {

// Expiry runs in the soft_timer IRQ drain — even a guest hung in a
// busy loop is interruptible there (CNTHP is a physical IRQ, HCR.IMO
// routes it to EL2). The reset is VM-wide and affinity-routed: the
// hung VM's vcpu 0 may live on another core.
void on_expiry(TrapContext* ctx, std::uint64_t vm) noexcept {
  console::write("[watchdog] VM ");
  console::write_dec64(vm);
  console::write(" missed its heartbeat window — resetting\n");
  smp::reset_vm(static_cast<std::size_t>(vm), ctx);
}

} // namespace

void watchdog_component::handle_hvc(HvcCall* call) noexcept {
  if (call->func_id != NOVA_HVC_FN_HEARTBEAT) {
    return; // not ours — leave unclaimed for other subscribers
  }
  call->handled = true;

  // The deadline is per VM but lives in THIS core's timer queue — the
  // heartbeat contract expects one heartbeating vCPU per VM (its boot
  // vCPU); a second one would arm an independent copy on its own core.
  const std::size_t   vm        = vm_of(vcpu::current_index());
  const std::uint64_t window_ms = call->ctx->x[1];
  if (window_ms == 0) {
    soft_timer::cancel(soft_timer::kSlotWatchdog + vm);
  } else {
    soft_timer::arm(soft_timer::kSlotWatchdog + vm, hyp_timer::now() + hyp_timer::freq() * window_ms / 1000U,
                    &on_expiry, static_cast<std::uint64_t>(vm));
  }
  call->ctx->x[0] = 0;
}

} // namespace nova
