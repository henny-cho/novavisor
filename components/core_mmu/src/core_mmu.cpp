// components/core_mmu/src/core_mmu.cpp
//
// Stage 2 MMU initialization and activation for the QEMU virt single-guest
// layout. Called once from core_mmu_component during RuntimeStart.
//
// References: ARM ARM DDI0487 §D13.2.138 (VTCR_EL2), §D13.2.137 (VTTBR_EL2),
// §D13.2.48 (HCR_EL2).

#include "components/core_mmu/include/core_mmu.hpp"

#include "components/core_mmu/include/stage2_builder.hpp"
#include "components/core_mmu/include/stage2_descriptor.hpp"
#include "hal/board_qemu_virt/include/uart.hpp"
#include "projects/qemu_virt_arm64/include/guest_config.hpp"

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
//   T0SZ  = 24    → 40-bit IPA (64 - 24)              bits 5:0
//   SL0   = 0b01  → start translation at L1            bits 7:6
//   IRGN0 = 0b11  → Normal WB RA WA (inner)            bits 9:8
//   ORGN0 = 0b11  → Normal WB RA WA (outer)            bits 11:10
//   SH0   = 0b11  → Inner Shareable                    bits 13:12
//   TG0   = 0b00  → 4 KiB granule                      bits 15:14
//   PS    = 0b010 → 40-bit physical address size       bits 18:16
//   VS    = 0     → 8-bit VMID                         bit 19
//   RES1                                               bit 31
inline constexpr std::uint64_t kVtcrEl2 = (24ULL) | (0b01ULL << 6U) | (0b11ULL << 8U) | (0b11ULL << 10U) |
                                          (0b11ULL << 12U) | (0b010ULL << 16U) | (1ULL << 31U);

// HCR_EL2:  VM=1 (Stage 2 translation enable, bit 0)
//           RW=1 (EL1 is AArch64, bit 31)
// All other EL1 trap/routing bits stay 0 in Phase 5; Phase 6 will set
// IMO/FMO for vIRQ routing.
inline constexpr std::uint64_t kHcrEl2 = (1ULL << 0U) | (1ULL << 31U);

} // namespace

// Defined in hal/armv8_aarch64/mmu.S.
// Writes VTTBR_EL2 / VTCR_EL2 / HCR_EL2 with barriers, invalidates Stage 2
// TLB for the current VMID.
extern "C" void nova_stage2_activate(std::uint64_t vttbr, std::uint64_t vtcr, std::uint64_t hcr) noexcept;

namespace mmu {

void init_and_activate() noexcept {
  const auto l1_pa = reinterpret_cast<std::uint64_t>(&stage2_l1);
  const auto l2_pa = reinterpret_cast<std::uint64_t>(&stage2_l2);
  const auto l3_pa = reinterpret_cast<std::uint64_t>(&stage2_l3);

  Stage2Tables tables{&stage2_l1, &stage2_l2, &stage2_l3, l2_pa, l3_pa};
  build_identity_map(tables, qemu_virt::kGuestIpaBase, qemu_virt::kGuestIpaSize, desc::kAttrNormalRwx);

  // VTTBR_EL2 layout:
  //   bits 47:1  BADDR (L1 table PA; bit 0 is always 0 for 4K-aligned)
  //   bits 55:48 VMID (8-bit when VTCR_EL2.VS == 0)
  const std::uint64_t vttbr = l1_pa | (static_cast<std::uint64_t>(qemu_virt::kGuestVmid) << 48U);

  nova_stage2_activate(vttbr, kVtcrEl2, kHcrEl2);

  board::qemu_virt::uart_write("Stage 2: activated VMID=1 IPA=0x50000000..0x50100000\n");
}

} // namespace mmu
} // namespace nova
