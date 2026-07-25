#pragma once

// hal/cache.hpp
//
// Cache maintenance for guest-visible code. When EL2 writes
// instructions a guest will execute (boot payload load, warm-reset
// pristine restore), the guest's instruction fetches are only
// guaranteed to see them after a clean-to-PoU + I-invalidate pass —
// the loader's half of the ARMv8 boot contract. QEMU models no
// caches, so a missing call here is invisible until real silicon.
//
// Reference: ARM ARM DDI0487 §B2.4.4 (instruction/data coherency),
// CTR_EL0.{IDC,DIC} (maintenance the implementation makes redundant).

#include <cstddef>
#include <cstdint>

namespace nova::cache {

// Make code just written to [pa, pa+size) fetchable by every PE:
// DC CVAU to PoU (skipped when CTR_EL0.IDC=1), then broadcast
// IC IVAU (skipped when CTR_EL0.DIC=1). The final ISB synchronizes
// this PE; guests are context-synchronized by the ERET that enters
// them, which is what makes the broadcast sufficient for other PEs.
inline void sync_guest_code(std::uintptr_t pa, std::size_t size) noexcept {
  if (size == 0) {
    return;
  }
  std::uint64_t ctr = 0;
  __asm__ volatile("mrs %0, ctr_el0" : "=r"(ctr));

  if ((ctr & (1ULL << 28)) == 0) { // IDC=0: clean to PoU is required
    const std::uintptr_t line = std::uintptr_t{4} << ((ctr >> 16) & 0xFU);
    for (std::uintptr_t addr = pa & ~(line - 1); addr < pa + size; addr += line) {
      __asm__ volatile("dc cvau, %0" ::"r"(addr));
    }
  }
  __asm__ volatile("dsb ish" ::: "memory");

  if ((ctr & (1ULL << 29)) == 0) { // DIC=0: I-cache invalidation is required
    const std::uintptr_t line = std::uintptr_t{4} << (ctr & 0xFU);
    for (std::uintptr_t addr = pa & ~(line - 1); addr < pa + size; addr += line) {
      __asm__ volatile("ic ivau, %0" ::"r"(addr));
    }
    __asm__ volatile("dsb ish" ::: "memory");
  }
  __asm__ volatile("isb" ::: "memory");
}

} // namespace nova::cache
