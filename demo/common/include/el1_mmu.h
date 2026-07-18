// NovaVisor demo EL1 Stage 1 MMU helper.
//
// Minimal guest-owned translation regime: a single level-1 table with
// 1 GiB block entries (4 KiB granule, T0SZ=32 so walks start at L1):
//   [0] VA 0x00000000 -> PA 0x00000000  Device   (emulated GIC frames)
//   [1] VA 0x40000000 -> PA 0x40000000  Normal   (guest RAM window)
//   [2] VA 0x80000000 -> PA 0x40000000  Normal   (ALIAS of [1])
// The alias slot is the point: after enabling, the guest can read its
// own data through VA = PA + EL1_MMU_ALIAS_OFFSET, which only works if
// translation is genuinely on.
//
// "PA" here is the guest's IPA — Stage 2 translates and isolates below
// this, tagged by VMID, whether the guest's Stage 1 is on or off.
//
// QEMU TCG does not model caches, so no cache maintenance is done
// around the SCTLR_EL1.{M,C,I} transition; real hardware would need to
// clean the table region and invalidate around the switch.

#ifndef NOVAVISOR_DEMO_EL1_MMU_H
#define NOVAVISOR_DEMO_EL1_MMU_H

#include <stdint.h>

#define EL1_MMU_ALIAS_OFFSET 0x40000000UL

// Level-1 block descriptor fields (4 KiB granule).
#define EL1_MMU_BLOCK       0x1UL
#define EL1_MMU_AF          (1UL << 10)
#define EL1_MMU_SH_INNER    (3UL << 8)
#define EL1_MMU_ATTR_NORMAL (0UL << 2) // MAIR attr0
#define EL1_MMU_ATTR_DEVICE (1UL << 2) // MAIR attr1

// MAIR_EL1: attr0 = Normal WB read/write-allocate, attr1 = Device-nGnRnE.
#define EL1_MMU_MAIR 0x00FFUL

// TCR_EL1: T0SZ=32, IRGN0/ORGN0 = WB WA, SH0 = inner shareable,
// EPD1 = 1 (no TTBR1 walks). TG0 = 0 (4 KiB), IPS = 0 (4 GiB PA).
#define EL1_MMU_TCR (32UL | (1UL << 8) | (1UL << 10) | (3UL << 12) | (1UL << 23))

// Populate `l1` (512 entries, 4 KiB-aligned) and switch Stage 1 on.
static inline void el1_mmu_enable(uint64_t l1[512]) {
  l1[0] = 0x00000000UL | EL1_MMU_BLOCK | EL1_MMU_AF | EL1_MMU_ATTR_DEVICE;
  l1[1] = 0x40000000UL | EL1_MMU_BLOCK | EL1_MMU_AF | EL1_MMU_SH_INNER | EL1_MMU_ATTR_NORMAL;
  l1[2] = 0x40000000UL | EL1_MMU_BLOCK | EL1_MMU_AF | EL1_MMU_SH_INNER | EL1_MMU_ATTR_NORMAL;
  for (int i = 3; i < 512; ++i) {
    l1[i] = 0;
  }

  __asm__ volatile("dsb ish" ::: "memory");
  __asm__ volatile("msr mair_el1, %0" ::"r"((uint64_t)EL1_MMU_MAIR));
  __asm__ volatile("msr tcr_el1, %0" ::"r"((uint64_t)EL1_MMU_TCR));
  __asm__ volatile("msr ttbr0_el1, %0" ::"r"((uint64_t)(uintptr_t)l1));
  __asm__ volatile("tlbi vmalle1\n\tdsb ish\n\tisb" ::: "memory");

  uint64_t sctlr;
  __asm__ volatile("mrs %0, sctlr_el1" : "=r"(sctlr));
  sctlr |= (1UL << 0) | (1UL << 2) | (1UL << 12); // M | C | I
  __asm__ volatile("msr sctlr_el1, %0" ::"r"(sctlr));
  __asm__ volatile("isb");
}

#endif // NOVAVISOR_DEMO_EL1_MMU_H
