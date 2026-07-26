#pragma once

// hal/arch/aarch64/timer.hpp
//
// Generic timer as the PE exposes it: CNTHP for the hypervisor, CNTV
// for the resident guest. No board specifics — the PPI INTIDs follow
// the standard assignment.
//
// The hypervisor owns the EL2 physical timer (CNTHP). Guests keep
// unrestricted access to the virtual counter/timer (CNTV; CNTVOFF is
// per-VM, rewritten on switch-in) and read-only access to the physical
// counter; programming the EL1 physical timer traps to EL2.

#include <cstdint>

namespace nova::arch::hyp_timer {

// The EL2 physical timer's PPI. The guest-visible CNTV PPI belongs to
// the guest-timer contract (the core_timer component).
inline constexpr std::uint32_t kHypTimerIntid = 26; // CNTHP

// CNTHCTL_EL2 (HCR_EL2.E2H = 0):
//   EL1PCTEN (bit 0) = 1 → EL1/EL0 may read the physical counter
//   EL1PCEN  (bit 1) = 0 → EL1 physical-timer programming traps to EL2
inline constexpr std::uint64_t kCnthctlEl1PhysCounterRead = 1ULL << 0;

// CNTHP_CTL_EL2: ENABLE (bit 0) = 1, IMASK (bit 1) = 0 → IRQ on expiry.
inline constexpr std::uint64_t kCnthpEnable = 1ULL << 0;

// CNT*_CTL ENABLE/IMASK bits (shared layout across the generic timers).
inline constexpr std::uint64_t kCntCtlEnable = 1ULL << 0;
inline constexpr std::uint64_t kCntCtlImask  = 1ULL << 1;

inline void init() noexcept {
  __asm__ volatile("msr cntvoff_el2, xzr"); // reset default — switch-in installs the VM's offset
  __asm__ volatile("msr cnthctl_el2, %0" ::"r"(kCnthctlEl1PhysCounterRead));
  __asm__ volatile("msr cnthp_ctl_el2, xzr"); // EL2 timer disarmed until first use
  __asm__ volatile("isb");
}

// Install a VM's virtual-counter offset (CNTVCT = CNTPCT - offset).
// Part of the switch-in register set, next to VTTBR/VMPIDR.
inline void write_cntvoff(std::uint64_t offset) noexcept {
  __asm__ volatile("msr cntvoff_el2, %0" ::"r"(offset));
}

// Physical counter. The leading ISB orders the read after preceding
// register writes so a just-reprogrammed timer never compares against
// a stale count.
[[nodiscard]] inline auto now() noexcept -> std::uint64_t {
  std::uint64_t cnt = 0;
  __asm__ volatile("isb; mrs %0, cntpct_el0" : "=r"(cnt));
  return cnt;
}

// Counter frequency in Hz (fixed by the platform, readable at any EL).
[[nodiscard]] inline auto freq() noexcept -> std::uint64_t {
  std::uint64_t f = 0;
  __asm__ volatile("mrs %0, cntfrq_el0" : "=r"(f));
  return f;
}

// Milliseconds → counter ticks. Multiplying before dividing keeps the
// sub-millisecond resolution of the counter; callers use timeouts far
// below the 64-bit overflow point (freq() is tens of MHz).
[[nodiscard]] inline auto ms_to_ticks(std::uint64_t ms) noexcept -> std::uint64_t {
  return freq() * ms / 1000U;
}

// Absolute counter value `ms` milliseconds from now — the shape every
// bounded wait and timer arm needs.
[[nodiscard]] inline auto deadline_after_ms(std::uint64_t ms) noexcept -> std::uint64_t {
  return now() + ms_to_ticks(ms);
}

// Arm the EL2 physical timer to fire (PPI kHypTimerIntid) at absolute
// counter value `cval`. An already-passed cval fires immediately.
inline void arm_at(std::uint64_t cval) noexcept {
  __asm__ volatile("msr cnthp_cval_el2, %0" ::"r"(cval));
  __asm__ volatile("msr cnthp_ctl_el2, %0" ::"r"(kCnthpEnable));
  __asm__ volatile("isb");
}

inline void stop() noexcept {
  __asm__ volatile("msr cnthp_ctl_el2, xzr");
  __asm__ volatile("isb");
}

// The resident guest's virtual-timer registers (live in hardware only
// while it is resident; parked VCPUs hold them in their EL1 bank).
[[nodiscard]] inline auto guest_cntv_ctl() noexcept -> std::uint64_t {
  std::uint64_t ctl = 0;
  __asm__ volatile("mrs %0, cntv_ctl_el0" : "=r"(ctl));
  return ctl;
}

[[nodiscard]] inline auto guest_cntv_cval() noexcept -> std::uint64_t {
  std::uint64_t cval = 0;
  __asm__ volatile("mrs %0, cntv_cval_el0" : "=r"(cval));
  return cval;
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

} // namespace nova::arch::hyp_timer
