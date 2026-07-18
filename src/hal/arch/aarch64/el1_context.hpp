#pragma once

// hal/arch/aarch64/el1_context.hpp
//
// EL1 system-register bank — the save/restore mechanism for guest state
// that lives outside the trap frame. Scheduling policy (when to switch)
// stays in core_vcpu; this header only knows how to move the registers.
//
// The bank is everything a guest may legally own at EL1/EL0: exception
// plumbing (VBAR, in-flight ELR/SPSR when a guest hypercalls from its
// own handler), the native virtual timer (CVAL, not TVAL — the absolute
// deadline survives being parked; a non-resident timer simply cannot
// fire), the full Stage 1 translation regime (SCTLR/TCR/TTBR0/TTBR1/
// MAIR/AMAIR/CONTEXTIDR — guests turn their own MMU on and off while
// time-shared; Stage 2 VMID tagging keeps their TLB entries apart),
// FP/software-thread plumbing (CPACR, TPIDR*), and fault reporting in
// flight at switch time (ESR/FAR/AFSR0/AFSR1/PAR).
//
// EL2 runs its own translation regime (SCTLR_EL2 et al.), so writing
// the incoming guest's values here has no effect until the ERET — a
// context synchronization event that also makes an ISB unnecessary.

#include <cstdint>

namespace nova::arch {

// SCTLR_EL1 reset: RES1 bits only (ARMv8.0 — bits 29:28, 23:22, 20,
// 11); MMU, caches, and alignment checking all off, EL1 is AArch64.
inline constexpr std::uint64_t kSctlrEl1Reset = 0x30D00800ULL;

struct El1SysregBank {
  std::uint64_t vbar      = 0;
  std::uint64_t elr       = 0;
  std::uint64_t spsr      = 0;
  std::uint64_t sp_el0    = 0;
  std::uint64_t cntv_ctl  = 0; // reset: timer disabled
  std::uint64_t cntv_cval = 0;
  // Stage 1 translation regime
  std::uint64_t sctlr      = kSctlrEl1Reset;
  std::uint64_t tcr        = 0;
  std::uint64_t ttbr0      = 0;
  std::uint64_t ttbr1      = 0;
  std::uint64_t mair       = 0;
  std::uint64_t amair      = 0;
  std::uint64_t contextidr = 0;
  // Access control + software thread IDs
  std::uint64_t cpacr     = 0; // reset: EL0/EL1 FP traps to the guest's EL1
  std::uint64_t tpidr_el0 = 0;
  std::uint64_t tpidrro   = 0;
  std::uint64_t tpidr_el1 = 0;
  // Fault reporting in flight at switch time
  std::uint64_t esr   = 0;
  std::uint64_t far   = 0;
  std::uint64_t afsr0 = 0;
  std::uint64_t afsr1 = 0;
  std::uint64_t par   = 0;
};

inline auto read_el1_bank() noexcept -> El1SysregBank {
  El1SysregBank b;
  __asm__ volatile("mrs %0, vbar_el1" : "=r"(b.vbar));
  __asm__ volatile("mrs %0, elr_el1" : "=r"(b.elr));
  __asm__ volatile("mrs %0, spsr_el1" : "=r"(b.spsr));
  __asm__ volatile("mrs %0, sp_el0" : "=r"(b.sp_el0));
  __asm__ volatile("mrs %0, cntv_ctl_el0" : "=r"(b.cntv_ctl));
  __asm__ volatile("mrs %0, cntv_cval_el0" : "=r"(b.cntv_cval));
  __asm__ volatile("mrs %0, sctlr_el1" : "=r"(b.sctlr));
  __asm__ volatile("mrs %0, tcr_el1" : "=r"(b.tcr));
  __asm__ volatile("mrs %0, ttbr0_el1" : "=r"(b.ttbr0));
  __asm__ volatile("mrs %0, ttbr1_el1" : "=r"(b.ttbr1));
  __asm__ volatile("mrs %0, mair_el1" : "=r"(b.mair));
  __asm__ volatile("mrs %0, amair_el1" : "=r"(b.amair));
  __asm__ volatile("mrs %0, contextidr_el1" : "=r"(b.contextidr));
  __asm__ volatile("mrs %0, cpacr_el1" : "=r"(b.cpacr));
  __asm__ volatile("mrs %0, tpidr_el0" : "=r"(b.tpidr_el0));
  __asm__ volatile("mrs %0, tpidrro_el0" : "=r"(b.tpidrro));
  __asm__ volatile("mrs %0, tpidr_el1" : "=r"(b.tpidr_el1));
  __asm__ volatile("mrs %0, esr_el1" : "=r"(b.esr));
  __asm__ volatile("mrs %0, far_el1" : "=r"(b.far));
  __asm__ volatile("mrs %0, afsr0_el1" : "=r"(b.afsr0));
  __asm__ volatile("mrs %0, afsr1_el1" : "=r"(b.afsr1));
  __asm__ volatile("mrs %0, par_el1" : "=r"(b.par));
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
  __asm__ volatile("msr sctlr_el1, %0" ::"r"(b.sctlr));
  __asm__ volatile("msr tcr_el1, %0" ::"r"(b.tcr));
  __asm__ volatile("msr ttbr0_el1, %0" ::"r"(b.ttbr0));
  __asm__ volatile("msr ttbr1_el1, %0" ::"r"(b.ttbr1));
  __asm__ volatile("msr mair_el1, %0" ::"r"(b.mair));
  __asm__ volatile("msr amair_el1, %0" ::"r"(b.amair));
  __asm__ volatile("msr contextidr_el1, %0" ::"r"(b.contextidr));
  __asm__ volatile("msr cpacr_el1, %0" ::"r"(b.cpacr));
  __asm__ volatile("msr tpidr_el0, %0" ::"r"(b.tpidr_el0));
  __asm__ volatile("msr tpidrro_el0, %0" ::"r"(b.tpidrro));
  __asm__ volatile("msr tpidr_el1, %0" ::"r"(b.tpidr_el1));
  __asm__ volatile("msr esr_el1, %0" ::"r"(b.esr));
  __asm__ volatile("msr far_el1, %0" ::"r"(b.far));
  __asm__ volatile("msr afsr0_el1, %0" ::"r"(b.afsr0));
  __asm__ volatile("msr afsr1_el1, %0" ::"r"(b.afsr1));
  __asm__ volatile("msr par_el1, %0" ::"r"(b.par));
  // No ISB needed here: the trap-return ERET is a context synchronization
  // event that makes these writes visible to the guest.
}

} // namespace nova::arch
