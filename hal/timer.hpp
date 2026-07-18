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

#include <cstdint>

namespace nova::hyp_timer {

// Standard generic-timer PPIs.
inline constexpr std::uint32_t kHypTimerIntid    = 26; // CNTHP  — EL2 physical timer
inline constexpr std::uint32_t kGuestTimerVintid = 27; // CNTV   — guest-visible virtual timer

// CNTHCTL_EL2 (HCR_EL2.E2H = 0):
//   EL1PCTEN (bit 0) = 1 → EL1/EL0 may read the physical counter
//   EL1PCEN  (bit 1) = 0 → EL1 physical-timer programming traps to EL2
inline constexpr std::uint64_t kCnthctlEl1PhysCounterRead = 1ULL << 0;

// CNTHP_CTL_EL2: ENABLE (bit 0) = 1, IMASK (bit 1) = 0 → IRQ on expiry.
inline constexpr std::uint64_t kCnthpEnable = 1ULL << 0;

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

} // namespace nova::hyp_timer
