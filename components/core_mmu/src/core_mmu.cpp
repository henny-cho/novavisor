// components/core_mmu/src/core_mmu.cpp
//
// Stage 2 MMU initialization and activation for the single boot guest
// described by the project-injected guest table (nova/guest.hpp).
// Called once from core_mmu_component during RuntimeStart.
//
// References: ARM ARM DDI0487 §D13.2.138 (VTCR_EL2), §D13.2.137 (VTTBR_EL2),
// §D13.2.48 (HCR_EL2).

#include "components/core_mmu/include/core_mmu.hpp"

#include "components/core_mmu/include/stage2_builder.hpp"
#include "components/core_mmu/include/stage2_descriptor.hpp"
#include "hal/console.hpp"
#include "nova/guest.hpp"

#include <cstdint>

namespace nova {
namespace {

// Stage 2 page tables. 4 KiB-aligned per-level tables live in BSS; their
// link-time addresses serve as the physical addresses written into
// Table descriptors (VTTBR points at L1; L1→L2 and L2→L3 are populated
// by build_identity_map).
alignas(mmu::k4KiB) mmu::Table stage2_l1;
alignas(mmu::k4KiB) mmu::Table stage2_l2;
alignas(mmu::k4KiB) mmu::Table stage2_l3;

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
// while stage2_l1 and l1_index() provide a single 512-entry 4 KiB table.
// With T0SZ=24 the VTTBR alignment check raises an Address size fault
// (level 1) whenever the linker happens to place stage2_l1 on a 4 KiB but
// not 8 KiB boundary.
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

} // namespace

// Defined in hal/armv8_aarch64/mmu.S.
// Writes VTTBR_EL2 / VTCR_EL2 / HCR_EL2 with barriers, invalidates Stage 2
// TLB for the current VMID.
extern "C" void nova_stage2_activate(std::uint64_t vttbr, std::uint64_t vtcr, std::uint64_t hcr) noexcept;

namespace mmu {

void init_and_activate() noexcept {
  // Phase 5/6: one set of Stage 2 tables for the sole boot guest.
  // Phase 7 (multi-VM) allocates one table set per guest-table entry.
  const GuestDescriptor& guest = guest_table().front();

  const auto l1_pa = reinterpret_cast<std::uint64_t>(&stage2_l1);
  const auto l2_pa = reinterpret_cast<std::uint64_t>(&stage2_l2);
  const auto l3_pa = reinterpret_cast<std::uint64_t>(&stage2_l3);

  Stage2Tables tables{.l1 = &stage2_l1, .l2 = &stage2_l2, .l3 = &stage2_l3, .l2_pa = l2_pa, .l3_pa = l3_pa};
  build_identity_map(tables, guest.ipa_base, guest.ipa_size, desc::kAttrNormalRwx);

  // VTTBR_EL2 layout:
  //   bits 47:1  BADDR (L1 table PA; bit 0 is always 0 for 4K-aligned)
  //   bits 55:48 VMID (8-bit when VTCR_EL2.VS == 0)
  const std::uint64_t vttbr = l1_pa | (static_cast<std::uint64_t>(guest.vmid) << 48U);

  nova_stage2_activate(vttbr, kVtcrEl2, kHcrEl2);

  // Status line is rendered from the descriptor so it can never drift
  // from the mapping that was actually installed.
  console::write("Stage 2: activated VMID=");
  console::write_dec64(guest.vmid);
  console::write(" IPA=0x");
  console::write_hex64(guest.ipa_base);
  console::write("..0x");
  console::write_hex64(guest.ipa_base + guest.ipa_size);
  console::write("\n");
}

} // namespace mmu
} // namespace nova
