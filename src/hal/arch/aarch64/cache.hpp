#pragma once

// hal/arch/aarch64/cache.hpp
//
// Cache maintenance for guest-visible memory. When EL2 writes memory a
// guest will consume (boot payload load, DTB, warm-reset pristine
// restore), the guest reads at the Point of Coherency — its MMU and
// caches are off at entry (SCTLR_EL1.M=0 makes every access
// non-cacheable), so PoU-scoped maintenance is not enough: DC CVAU (or
// the CTR_EL0.IDC=1 shortcut, which only promises data-to-instruction
// coherency) can leave a dirty line above the PoC and DRAM stale for
// the guest's very first read. Clean to PoC unconditionally. QEMU
// models no caches, so a missing or too-weak call here is invisible
// until real silicon.
//
// Reference: ARM ARM DDI0487 §B2.4.4 (instruction/data coherency),
// CTR_EL0.DIC (I-side maintenance the implementation makes redundant).

#include <cstddef>
#include <cstdint>

namespace nova::arch::cache {

// Raw CTR_EL0 — line geometry and maintenance shortcuts of this PE.
// Also consumed by the SMP bring-up to enforce the homogeneous-cluster
// premise (every CMO loop uses the executing PE's line size).
[[nodiscard]] inline auto ctr() noexcept -> std::uint64_t {
  std::uint64_t v = 0;
  __asm__ volatile("mrs %0, ctr_el0" : "=r"(v));
  return v;
}

// Make data just written to [pa, pa+size) visible to a guest reading
// at the PoC (MMU-off boot code parsing its DTB, non-cacheable early
// state). DC CVAC per DminLine, completed with DSB ISH.
inline void clean_guest_data(std::uintptr_t pa, std::size_t size) noexcept {
  if (size == 0) {
    return;
  }
  const std::uint64_t  v    = ctr();
  const std::uintptr_t line = std::uintptr_t{4} << ((v >> 16) & 0xFU);
  for (std::uintptr_t addr = pa & ~(line - 1); addr < pa + size; addr += line) {
    __asm__ volatile("dc cvac, %0" ::"r"(addr));
  }
  __asm__ volatile("dsb ish" ::: "memory");
}

// Make code just written to [pa, pa+size) fetchable by every PE:
// clean to PoC, then broadcast IC IVAU (skipped when CTR_EL0.DIC=1 —
// the I-side tracks writes in hardware, so stale lines from a previous
// guest generation cannot survive either). The final ISB synchronizes
// this PE; guests are context-synchronized by the ERET that enters
// them, which is what makes the broadcast sufficient for other PEs.
inline void sync_guest_code(std::uintptr_t pa, std::size_t size) noexcept {
  if (size == 0) {
    return;
  }
  clean_guest_data(pa, size);

  const std::uint64_t v = ctr();
  if ((v & (1ULL << 29)) == 0) { // DIC=0: I-cache invalidation is required
    const std::uintptr_t line = std::uintptr_t{4} << (v & 0xFU);
    for (std::uintptr_t addr = pa & ~(line - 1); addr < pa + size; addr += line) {
      __asm__ volatile("ic ivau, %0" ::"r"(addr));
    }
    __asm__ volatile("dsb ish" ::: "memory");
  }
  __asm__ volatile("isb" ::: "memory");
}

} // namespace nova::arch::cache
