#pragma once

// Boot-time hardware-parameter contract, shared by every board profile.
//
// Runs before cib's RuntimeStart, next to init_guest_table(), and that
// position is the point: the frequency adopted here backs every bounded
// wait in the hypervisor, including the register-write-pending budgets
// the physical GIC bring-up spends — and that bring-up is the second
// action in the RuntimeStart chain. Adopting inside the chain would put
// the gate after its first consumer.
//
// Stage-2 translation parameters (PARange, granule, VMID width) are
// gated separately in core_mmu, where the VTCR constants they must match
// are defined. Each gate sits next to what it protects.

#include "hal/console.hpp"
#include "hal/cpu.hpp"
#include "hal/timer.hpp"
#include "nova/arch/timebase.hpp"
#include "nova/panic.hpp"

namespace nova::boot_contract {

// Adopt the machine timebase and decode the speculation-barrier
// features. A timebase the hardware does not report usably has no safe
// fallback — an unprogrammed CNTFRQ_EL0 makes every deadline equal to
// "now", which the time slice, the secondary online wait and every
// quiesce budget would each read as an immediate expiry — so this stops
// the boot with the raw value in the report instead.
inline void enforce() noexcept {
  if (const arch::TimebaseError error = hyp_timer::adopt_timebase(); error != arch::TimebaseError::kNone) {
    console::line("[NOVA PANIC] timebase: ", arch::to_string(error),
                  " (CNTFRQ_EL0=", console::Dec{hyp_timer::raw_freq()}, ")\n");
    halt();
  }
  cpu::adopt_speculation_state();
}

} // namespace nova::boot_contract
