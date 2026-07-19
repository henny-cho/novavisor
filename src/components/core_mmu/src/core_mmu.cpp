// components/core_mmu/src/core_mmu.cpp
//
// Stage 2 MMU initialization and activation. Builds one table set per
// guest_table() entry (nova/abi/guest.hpp) during RuntimeStart, activates
// the boot guest's set, and provides the VTTBR switch used by the VCPU
// scheduler.
//
// References: ARM ARM DDI0487 §D13.2.138 (VTCR_EL2), §D13.2.137 (VTTBR_EL2),
// §D13.2.48 (HCR_EL2).

#include "core_mmu/core_mmu.hpp"

#include "core_mmu/stage2_builder.hpp"
#include "core_mmu/stage2_descriptor.hpp"
#include "hal/console.hpp"
#include "nova/abi/guest.hpp"
#include "nova/abi/guest_layout.h"
#include "nova_panic/nova_panic.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>

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
//           TWI=1 (trap EL1/EL0 WFI to EL2, bit 13)
//           TWE=1 (trap EL1/EL0 WFE to EL2, bit 14)
//           TSC=1 (trap EL1 SMC to EL2, bit 19)
//           RW=1  (EL1 is AArch64, bit 31)
// IMO/FMO make the hypervisor the sole owner of physical interrupts —
// guests see only vINTIDs injected via ICH_LR (components/core_gic) —
// and additionally expose the virtual interrupt registers (ICV_*) to
// EL1 in place of the physical ICC_* ones. TWI/TWE hand guest waits to
// the scheduler (WfxService) instead of stalling the physical core.
// TSC gives us a second PSCI conduit: this board has no EL3, so an
// untrapped guest SMC would just be an undefined instruction — we trap
// it and serve it through the same dispatch as HVC.
inline constexpr std::uint64_t kHcrEl2 =
    (1ULL << 0U) | (1ULL << 3U) | (1ULL << 4U) | (1ULL << 13U) | (1ULL << 14U) | (1ULL << 19U) | (1ULL << 31U);

// VTTBR_EL2 layout:
//   bits 47:1  BADDR (L1 table PA; bit 0 is always 0 for 4K-aligned)
//   bits 55:48 VMID (8-bit when VTCR_EL2.VS == 0)
inline constexpr std::uint64_t kVttbrVmidShift = 48U;

// Pristine slot backing guest `index` — EL2's flat view of the PAs
// reserved in nova/abi/guest_layout.h. Windows may differ in size per
// guest, so slots pack at the running sum of the preceding windows.
auto pristine_slot(std::size_t index) noexcept -> void* {
  const auto    guests = guest_table();
  std::uint64_t offset = 0;
  for (std::size_t i = 0; i < index; ++i) {
    offset += guests[i].ipa_size;
  }
  return reinterpret_cast<void*>(NOVA_GUEST_PRISTINE_PA + offset);
}

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

// Defined in hal/arch/aarch64/mmu.S.
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

  // Place each guest's configuration blob at its window-top slot so
  // the pristine snapshot below captures it — a warm reset then
  // restores DTB and image together through the same copy.
  for (std::size_t i = 0; i < guests.size(); ++i) {
    if (guests[i].dtb_size != 0) {
      std::memcpy(reinterpret_cast<void*>(guests[i].to_pa(guests[i].dtb_ipa)), guests[i].dtb, guests[i].dtb_size);
    }
  }

  // Preserve every guest window for warm reset. The whole window is
  // copied (the hypervisor does not know the binary's size); at this
  // point it holds the loader's image plus zeroed RAM — exactly the
  // state a reboot must restore.
  for (std::size_t i = 0; i < guests.size(); ++i) {
    std::memcpy(pristine_slot(i), reinterpret_cast<const void*>(guests[i].load_pa),
                static_cast<std::size_t>(guests[i].ipa_size));
  }

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

void activate_cpu() noexcept {
  // VTCR/VTTBR/HCR are banked per PE — a secondary programs its own
  // from the tables the primary built (immutable since). The initial
  // VTTBR is a placeholder; the first switch_vm retargets it.
  nova_stage2_activate(g_vttbr[0], kVtcrEl2, kHcrEl2);
}

void switch_vm(std::size_t guest_index) noexcept {
  nova_stage2_switch(g_vttbr[guest_index]);
}

void reload_guest_image(std::size_t guest_index) noexcept {
  const GuestDescriptor& guest = guest_table()[guest_index];
  std::memcpy(reinterpret_cast<void*>(guest.load_pa), pristine_slot(guest_index),
              static_cast<std::size_t>(guest.ipa_size));
}

} // namespace mmu
} // namespace nova
