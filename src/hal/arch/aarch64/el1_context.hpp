#pragma once

// hal/arch/aarch64/el1_context.hpp
//
// EL1 system-register bank — the save/restore mechanism for guest state
// that lives outside the trap frame. Scheduling policy (when to switch)
// stays in core_vcpu; this header only knows how to move the registers.
//
// TrapContext covers what the EL2 trap banked: guests own VBAR_EL1
// (their vectors), and ELR/SPSR_EL1 are in flight when a guest
// hypercalls from inside its own IRQ handler. CNTV_CTL/CVAL bank the
// guest's native virtual timer (CVAL, not TVAL — the absolute deadline
// survives the time spent parked; a non-resident timer simply cannot
// fire). MMU-off flat guests have no further EL1 state; SCTLR_EL1-class
// registers join the bank when such guests get time-shared (backlog).

#include <cstdint>

namespace nova::arch {

struct El1SysregBank {
  std::uint64_t vbar      = 0;
  std::uint64_t elr       = 0;
  std::uint64_t spsr      = 0;
  std::uint64_t sp_el0    = 0;
  std::uint64_t cntv_ctl  = 0; // reset: timer disabled
  std::uint64_t cntv_cval = 0;
};

inline auto read_el1_bank() noexcept -> El1SysregBank {
  El1SysregBank b;
  __asm__ volatile("mrs %0, vbar_el1" : "=r"(b.vbar));
  __asm__ volatile("mrs %0, elr_el1" : "=r"(b.elr));
  __asm__ volatile("mrs %0, spsr_el1" : "=r"(b.spsr));
  __asm__ volatile("mrs %0, sp_el0" : "=r"(b.sp_el0));
  __asm__ volatile("mrs %0, cntv_ctl_el0" : "=r"(b.cntv_ctl));
  __asm__ volatile("mrs %0, cntv_cval_el0" : "=r"(b.cntv_cval));
  return b;
}

inline void write_el1_bank(const El1SysregBank& b) noexcept {
  __asm__ volatile("msr vbar_el1, %0" ::"r"(b.vbar));
  __asm__ volatile("msr elr_el1, %0" ::"r"(b.elr));
  __asm__ volatile("msr spsr_el1, %0" ::"r"(b.spsr));
  __asm__ volatile("msr sp_el0, %0" ::"r"(b.sp_el0));
  // CVAL before CTL so a still-armed timer re-evaluates its condition
  // against the incoming guest's deadline, never the outgoing one's.
  __asm__ volatile("msr cntv_cval_el0, %0" ::"r"(b.cntv_cval));
  __asm__ volatile("msr cntv_ctl_el0, %0" ::"r"(b.cntv_ctl));
  // No ISB needed here: the trap-return ERET is a context synchronization
  // event that makes these writes visible to the guest.
}

} // namespace nova::arch
