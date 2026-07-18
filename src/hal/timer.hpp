#pragma once

// hal/timer.hpp
//
// Generic-timer facade for the hypervisor (ARMv8 architectural timers —
// no board specifics; PPI INTIDs follow the standard SBSA assignment
// QEMU virt uses).
//
// The hypervisor owns the EL2 physical timer (CNTHP). Guests keep
// unrestricted access to the virtual counter/timer (CNTV, CNTVOFF = 0)
// and read-only access to the physical counter; programming the EL1
// physical timer traps to EL2.

#include "nova/abi/hvc_abi.h"

#include <cstdint>

namespace nova::hyp_timer {

// Standard generic-timer PPIs. The guest-visible one is fixed by the
// ABI contract — physical PPI 27 and the injected vINTID coincide.
inline constexpr std::uint32_t kHypTimerIntid    = 26;                // CNTHP — EL2 physical timer
inline constexpr std::uint32_t kGuestTimerVintid = NOVA_TIMER_VINTID; // CNTV

// CNTHCTL_EL2 (HCR_EL2.E2H = 0):
//   EL1PCTEN (bit 0) = 1 → EL1/EL0 may read the physical counter
//   EL1PCEN  (bit 1) = 0 → EL1 physical-timer programming traps to EL2
inline constexpr std::uint64_t kCnthctlEl1PhysCounterRead = 1ULL << 0;

// CNTHP_CTL_EL2: ENABLE (bit 0) = 1, IMASK (bit 1) = 0 → IRQ on expiry.
inline constexpr std::uint64_t kCnthpEnable = 1ULL << 0;

// CNT*_CTL IMASK bit (shared layout across the generic timers).
inline constexpr std::uint64_t kCntCtlImask = 1ULL << 1;

inline void init() noexcept {
  __asm__ volatile("msr cntvoff_el2, xzr"); // guest virtual counter == physical
  __asm__ volatile("msr cnthctl_el2, %0" ::"r"(kCnthctlEl1PhysCounterRead));
  __asm__ volatile("msr cnthp_ctl_el2, xzr"); // EL2 timer disarmed until first use
  __asm__ volatile("isb");
}

// Arm the EL2 physical timer to fire (PPI kHypTimerIntid) in `ticks`
// counter cycles from now.
inline void arm(std::uint64_t ticks) noexcept {
  __asm__ volatile("msr cnthp_tval_el2, %0" ::"r"(ticks));
  __asm__ volatile("msr cnthp_ctl_el2, %0" ::"r"(kCnthpEnable));
  __asm__ volatile("isb");
}

inline void stop() noexcept {
  __asm__ volatile("msr cnthp_ctl_el2, xzr");
  __asm__ volatile("isb");
}

// Mask the (level-triggered) virtual timer of the resident guest. CNTV
// keeps asserting its PPI while the expiry condition holds; without
// this it would re-fire forever after EL2 EOIs. The guest unmasks
// itself by rewriting CNTV_CTL_EL0 when it re-arms.
inline void mask_guest_virtual_timer() noexcept {
  std::uint64_t ctl = 0;
  __asm__ volatile("mrs %0, cntv_ctl_el0" : "=r"(ctl));
  ctl |= kCntCtlImask;
  __asm__ volatile("msr cntv_ctl_el0, %0" ::"r"(ctl));
  __asm__ volatile("isb");
}

} // namespace nova::hyp_timer
