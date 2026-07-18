// components/core_mmu/src/core_mmu.cpp
//
// Stage 2 MMU initialization and activation. Builds one table set per
// guest_table() entry (nova/abi/guest.hpp) during RuntimeStart, activates
// the boot guest's set, and provides the VTTBR switch used by the VCPU
// scheduler.
//
// References: ARM ARM DDI0487 §D13.2.138 (VTCR_EL2), §D13.2.137 (VTTBR_EL2),
// §D13.2.48 (HCR_EL2).

#include "components/core_mmu/include/core_mmu.hpp"

#include "components/core_mmu/include/stage2_builder.hpp"
#include "components/core_mmu/include/stage2_descriptor.hpp"
#include "components/nova_panic/include/nova_panic.hpp"
#include "hal/console.hpp"
#include "nova/abi/guest.hpp"
#include "nova/abi/guest_layout.h"

#include <array>
#include <cstddef>
#include <cstdint>

namespace nova {
namespace {

// Stage 2 page tables, one set per possible guest. 4 KiB-aligned tables
// live in BSS; their link-time addresses serve as the physical addresses
// written into Table descriptors (VTTBR points at l1; l1→l2 and l2→l3
// are populated by map_range).
//
// The L3 pool serves sub-2 MiB fragments only — the 1 MiB guest window
// and the IVC shared page land in the same 2 MiB slot, so one pool
// table suffices today; the second is headroom for window growth.
inline constexpr std::size_t kL3PoolSize = 2;

struct TableSet {
  mmu::Table                          l1;
  mmu::Table                          l2;
  std::array<mmu::Table, kL3PoolSize> l3_pool;
};

alignas(mmu::k4KiB) std::array<TableSet, kMaxGuests> stage2_sets;

// VTTBR_EL2 value per guest, precomputed at build time for the switch
// path. Index parallels guest_table().
std::array<std::uint64_t, kMaxGuests> g_vttbr{};

// VTCR_EL2 field encoding:
//   T0SZ  = 25    → 39-bit IPA (64 - 25)              bits 5:0
//   SL0   = 0b01  → start translation at L1            bits 7:6
//   IRGN0 = 0b11  → Normal WB RA WA (inner)            bits 9:8
//   ORGN0 = 0b11  → Normal WB RA WA (outer)            bits 11:10
//   SH0   = 0b11  → Inner Shareable                    bits 13:12
//   TG0   = 0b00  → 4 KiB granule                      bits 15:14
//   PS    = 0b010 → 40-bit physical address size       bits 18:16
//   VS    = 0     → 8-bit VMID                         bit 19
//   RES1                                               bit 31
//
// T0SZ must be 25, not 24: a 40-bit IPA starting at L1 would index IPA
// bits [39:30] — a 1024-entry (8 KiB, 8 KiB-aligned) concatenated L1 table,
// while the TableSet L1s and l1_index() provide single 512-entry 4 KiB
// tables. With T0SZ=24 the VTTBR alignment check raises an Address size
// fault (level 1) whenever the linker happens to place an L1 on a 4 KiB
// but not 8 KiB boundary.
inline constexpr std::uint64_t kVtcrEl2 = (25ULL) | (0b01ULL << 6U) | (0b11ULL << 8U) | (0b11ULL << 10U) |
                                          (0b11ULL << 12U) | (0b010ULL << 16U) | (1ULL << 31U);

// HCR_EL2:  VM=1  (Stage 2 translation enable, bit 0)
//           FMO=1 (route physical FIQ to EL2, bit 3)
//           IMO=1 (route physical IRQ to EL2, bit 4)
//           RW=1  (EL1 is AArch64, bit 31)
// IMO/FMO make the hypervisor the sole owner of physical interrupts —
// guests see only vINTIDs injected via ICH_LR (components/core_gic) —
// and additionally expose the virtual interrupt registers (ICV_*) to
// EL1 in place of the physical ICC_* ones.
inline constexpr std::uint64_t kHcrEl2 = (1ULL << 0U) | (1ULL << 3U) | (1ULL << 4U) | (1ULL << 31U);

// VTTBR_EL2 layout:
//   bits 47:1  BADDR (L1 table PA; bit 0 is always 0 for 4K-aligned)
//   bits 55:48 VMID (8-bit when VTCR_EL2.VS == 0)
inline constexpr std::uint64_t kVttbrVmidShift = 48U;

[[noreturn]] void panic_stage2(std::size_t guest_index) noexcept {
  console::write("[NOVA PANIC] Stage 2 map failed for guest ");
  console::write_dec64(guest_index);
  console::write(" (range/pool constraints)\n");
  halt();
}

// Populate one guest's table set: its window (IPA → PA slot) plus the
// IVC shared page (same IPA in every VM, RW non-executable).
void build_guest_tables(std::size_t index, const GuestDescriptor& guest) noexcept {
  TableSet& set = stage2_sets[index];

  std::array<std::uint64_t, kL3PoolSize> l3_pas{};
  for (std::size_t i = 0; i < kL3PoolSize; ++i) {
    l3_pas[i] = reinterpret_cast<std::uint64_t>(&set.l3_pool[i]);
  }

  mmu::Stage2Tables tables{.l1          = &set.l1,
                           .l2          = &set.l2,
                           .l2_pa       = reinterpret_cast<std::uint64_t>(&set.l2),
                           .l3_pool     = set.l3_pool,
                           .l3_pool_pas = l3_pas};
  mmu::init_tables(tables);

  if (!mmu::map_range(tables, guest.ipa_base, guest.load_pa, guest.ipa_size, mmu::desc::kAttrNormalRwx) ||
      !mmu::map_range(tables, NOVA_IVC_SHM_IPA, NOVA_IVC_SHM_PA, NOVA_IVC_SHM_SIZE, mmu::desc::kAttrNormalRwData)) {
    panic_stage2(index);
  }

  g_vttbr[index] =
      reinterpret_cast<std::uint64_t>(&set.l1) | (static_cast<std::uint64_t>(guest.vmid) << kVttbrVmidShift);
}

} // namespace

// Defined in hal/armv8_aarch64/mmu.S.
// Writes VTTBR_EL2 / VTCR_EL2 / HCR_EL2 with barriers, invalidates the
// Stage 1+2 TLB for all VMIDs (nova_stage2_activate) or retargets
// VTTBR_EL2 only (nova_stage2_switch — VMID tagging isolates the TLB,
// and the static tables never change after build).
extern "C" void nova_stage2_activate(std::uint64_t vttbr, std::uint64_t vtcr, std::uint64_t hcr) noexcept;
extern "C" void nova_stage2_switch(std::uint64_t vttbr) noexcept;

namespace mmu {

void init_and_activate() noexcept {
  const auto guests = guest_table();
  if (guests.empty() || guests.size() > kMaxGuests) {
    console::write("[NOVA PANIC] guest_table size out of range\n");
    halt();
  }

  for (std::size_t i = 0; i < guests.size(); ++i) {
    build_guest_tables(i, guests[i]);
  }

  nova_stage2_activate(g_vttbr[0], kVtcrEl2, kHcrEl2);

  // Status line is rendered from the descriptors so it can never drift
  // from the mappings that were actually installed.
  for (std::size_t i = 0; i < guests.size(); ++i) {
    console::write("Stage 2: VMID=");
    console::write_dec64(guests[i].vmid);
    console::write(" IPA=0x");
    console::write_hex64(guests[i].ipa_base);
    console::write("..0x");
    console::write_hex64(guests[i].ipa_base + guests[i].ipa_size);
    console::write(" PA=0x");
    console::write_hex64(guests[i].load_pa);
    console::write(i == 0 ? " (active)\n" : "\n");
  }
}

void switch_vm(std::size_t guest_index) noexcept {
  nova_stage2_switch(g_vttbr[guest_index]);
}

} // namespace mmu
} // namespace nova
