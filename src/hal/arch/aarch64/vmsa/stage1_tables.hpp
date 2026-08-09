#pragma once

// hal/arch/aarch64/vmsa/stage1_tables.hpp
//
// EL2 Stage-1 identity page tables for ARMv8-A, 4KB granule.
// Header-only, no privileged instructions — host-testable.
//
// Reference: ARM ARM DDI0487 §D8.3 (VMSAv8-64 descriptor formats),
// §D8.2.8 (EL2 translation regime).
//
// Geometry: T0SZ=32 (4 GiB VA = PA, identity), walk starts at L1 with
// four 1 GiB entries. Both boards keep every EL2-visible physical
// address below 4 GiB (el2_mmu.cpp static_asserts this), so a single
// L1 table plus a small pool of L2/L3 tables covers the whole map.
//
// Descriptor bit layout (Stage 1, MAIR-indexed — distinct from the
// Stage 2 encoding in core_mmu/stage2_descriptor.hpp):
//   [1:0]   type       00=Invalid, 01=Block (L1/L2), 11=Table (L1/L2) or Page (L3)
//   [4:2]   AttrIndx   index into MAIR_EL2
//   [7:6]   AP         AP[1] is RES1 in single-EL regimes; AP[2]=1 read-only
//   [9:8]   SH         00 non-, 10 outer-, 11 inner-shareable
//   [10]    AF         access flag
//   [47:12] output address
//   [54]    XN         execute-never

#include "hal/arch/aarch64/vmsa/stage1_regs.h"

#include <array>
#include <cstdint>
#include <span>

namespace nova::arch::stage1 {

// --- Walk geometry ------------------------------------------------------------

inline constexpr std::uint64_t kVaLimit  = 1ULL << 32; // T0SZ=32
inline constexpr std::uint64_t kPageSize = 1ULL << 12;
inline constexpr std::uint64_t kBlockL2  = 1ULL << 21;
inline constexpr std::uint64_t kBlockL1  = 1ULL << 30;
inline constexpr std::size_t   kEntries  = 512;

struct alignas(4096) Table {
  std::array<std::uint64_t, kEntries> entry;
};

namespace desc {

inline constexpr std::uint64_t kTypeMask    = 0b11ULL;
inline constexpr std::uint64_t kTypeInvalid = 0b00ULL;
inline constexpr std::uint64_t kTypeBlock   = 0b01ULL; // L1, L2
inline constexpr std::uint64_t kTypeTable   = 0b11ULL; // L1, L2
inline constexpr std::uint64_t kTypePage    = 0b11ULL; // L3

inline constexpr std::uint64_t kAttrIndxShift = 2;
inline constexpr std::uint64_t kAttrIndxMask  = 0b111ULL << kAttrIndxShift;

// MAIR_EL2 slots (values pinned against NOVA_EL2_MAIR below).
inline constexpr std::uint64_t kAttrIndxDevice = 0; // Device-nGnRE
inline constexpr std::uint64_t kAttrIndxNormal = 1; // Normal WB RA WA

// The EL2 regime has a single privilege level: AP[1] is RES1 and only
// AP[2] (bit 7) carries meaning (1 = read-only).
inline constexpr std::uint64_t kApRes1     = 1ULL << 6;
inline constexpr std::uint64_t kApReadOnly = 1ULL << 7;

inline constexpr std::uint64_t kShShift          = 8;
inline constexpr std::uint64_t kShMask           = 0b11ULL << kShShift;
inline constexpr std::uint64_t kShInnerShareable = 0b11ULL;

inline constexpr std::uint64_t kAfBit = 1ULL << 10;
inline constexpr std::uint64_t kXnBit = 1ULL << 54;

inline constexpr std::uint64_t kOutputAddrMask = 0x0000'FFFF'FFFF'F000ULL;

// --- Attribute presets ---------------------------------------------------------

// MMIO. Device memory forbids speculative data access, so unused
// device-mapped space is harmless; XN and non-shareable by convention.
inline constexpr std::uint64_t kAttrDevice = (kAttrIndxDevice << kAttrIndxShift) | kApRes1 | kAfBit | kXnBit;

// RAM the hypervisor reads and writes. Inner-shareable WB cacheable is
// what makes exclusives (locks, atomics) architecturally valid.
inline constexpr std::uint64_t kAttrNormalRw =
    (kAttrIndxNormal << kAttrIndxShift) | kApRes1 | (kShInnerShareable << kShShift) | kAfBit | kXnBit;

// .text: read-only, executable.
inline constexpr std::uint64_t kAttrNormalRx =
    (kAttrIndxNormal << kAttrIndxShift) | kApRes1 | kApReadOnly | (kShInnerShareable << kShShift) | kAfBit;

// .rodata: read-only, never executable.
inline constexpr std::uint64_t kAttrNormalRo = kAttrNormalRx | kXnBit;

} // namespace desc

// --- Register values (named-field derivation, pinned to stage1_regs.h) --------

inline constexpr std::uint64_t kMairDeviceNGnRE = 0x04;
inline constexpr std::uint64_t kMairNormalWb    = 0xFF;
inline constexpr std::uint64_t kMairEl2 =
    (kMairDeviceNGnRE << (8 * desc::kAttrIndxDevice)) | (kMairNormalWb << (8 * desc::kAttrIndxNormal));
static_assert(kMairEl2 == NOVA_EL2_MAIR);

inline constexpr std::uint64_t kTcrEl2 = (1ULL << 31) | (1ULL << 23) // RES1
                                         | (0ULL << 16)              // PS: 32-bit PA
                                         | (0ULL << 14)              // TG0: 4KB
                                         | (3ULL << 12)              // SH0: inner shareable
                                         | (1ULL << 10)              // ORGN0: WB RA WA
                                         | (1ULL << 8)               // IRGN0: WB RA WA
                                         | 32ULL;                    // T0SZ
static_assert(kTcrEl2 == NOVA_EL2_TCR);

// Writable implies execute-never: the whole of this regime's W^X.
// Named rather than left a bit position in the sum below, because a
// reader asking whether EL2 forbids W&X asks for this bit.
inline constexpr std::uint64_t kSctlrWxn = 1ULL << 19;

inline constexpr std::uint64_t kSctlrEl2 = 0x30C50830ULL  // RES1
                                           | (1ULL << 0)  // M: Stage-1 MMU
                                           | (1ULL << 2)  // C: data cache
                                           | (1ULL << 3)  // SA: SP alignment check
                                           | (1ULL << 12) // I: instruction cache
                                           | kSctlrWxn;
static_assert(kSctlrEl2 == NOVA_EL2_SCTLR);

// The span the builder refuses to map past and the span the hardware
// walks are one fact: T0SZ is where the walker learns it. W^X is stated
// twice for the same reason — the attribute presets place XN on every
// writable mapping, and WXN makes the MMU enforce it whatever a future
// preset says.
static_assert(
    [] {
      const std::uint64_t t0sz = kTcrEl2 & 0x3FU; // TCR_EL2.T0SZ [5:0]
      return (1ULL << (64U - t0sz)) == kVaLimit && (kSctlrEl2 & kSctlrWxn) != 0;
    }(),
    "the EL2 translation registers describe the map this builder produces");

// --- Identity map builder ------------------------------------------------------
//
// Maps disjoint [base, end) ranges over a caller-provided root + table
// pool, picking the largest block size alignment allows (1 GiB → 2 MiB
// → 4 KiB). Ranges must not overlap anything already mapped; a mapped
// entry in the way is an error, so a bad range list fails loudly
// instead of silently changing attributes.

class Stage1Builder {
public:
  Stage1Builder(Table& root, std::span<Table> pool) noexcept : root_{&root}, pool_{pool} {
    for (auto& e : root_->entry) {
      e = 0;
    }
  }

  // Identity-map [base, end) with `attrs`. Empty ranges succeed (a
  // section boundary can coincide with the RAM base). Returns false on
  // misalignment, overlap, VA overflow, or pool exhaustion.
  [[nodiscard]] auto map(std::uint64_t base, std::uint64_t end, std::uint64_t attrs) noexcept -> bool {
    if (base > end || end > kVaLimit || (base % kPageSize) != 0 || (end % kPageSize) != 0) {
      return false;
    }
    while (base < end) {
      std::uint64_t& l1e = root_->entry[index(base, kBlockL1)];
      if ((base % kBlockL1) == 0 && end - base >= kBlockL1) {
        if (!place(l1e, base, attrs, desc::kTypeBlock)) {
          return false;
        }
        base += kBlockL1;
        continue;
      }
      Table* l2 = descend(l1e);
      if (l2 == nullptr) {
        return false;
      }
      std::uint64_t& l2e = l2->entry[index(base, kBlockL2)];
      if ((base % kBlockL2) == 0 && end - base >= kBlockL2) {
        if (!place(l2e, base, attrs, desc::kTypeBlock)) {
          return false;
        }
        base += kBlockL2;
        continue;
      }
      Table* l3 = descend(l2e);
      if (l3 == nullptr) {
        return false;
      }
      std::uint64_t& l3e = l3->entry[index(base, kPageSize)];
      if (!place(l3e, base, attrs, desc::kTypePage)) {
        return false;
      }
      base += kPageSize;
    }
    return true;
  }

  [[nodiscard]] auto tables_used() const noexcept -> std::size_t { return used_; }

private:
  static auto index(std::uint64_t va, std::uint64_t block) noexcept -> std::size_t {
    return static_cast<std::size_t>((va / block) % kEntries);
  }

  static auto place(std::uint64_t& slot, std::uint64_t pa, std::uint64_t attrs, std::uint64_t type) noexcept -> bool {
    if ((slot & desc::kTypeMask) != desc::kTypeInvalid) {
      return false; // overlap with an existing mapping
    }
    slot = (pa & desc::kOutputAddrMask) | attrs | type;
    return true;
  }

  // Return the next-level table behind `slot`, allocating one from the
  // pool when the slot is invalid. A block in the slot returns nullptr
  // (the caller's range collides with a coarser mapping).
  auto descend(std::uint64_t& slot) noexcept -> Table* {
    const std::uint64_t type = slot & desc::kTypeMask;
    if (type == desc::kTypeTable) {
      return reinterpret_cast<Table*>(static_cast<std::uintptr_t>(slot & desc::kOutputAddrMask));
    }
    if (type != desc::kTypeInvalid || used_ == pool_.size()) {
      return nullptr;
    }
    Table* next = &pool_[used_++];
    for (auto& e : next->entry) {
      e = 0;
    }
    slot = (reinterpret_cast<std::uintptr_t>(next) & desc::kOutputAddrMask) | desc::kTypeTable;
    return next;
  }

  Table*           root_;
  std::span<Table> pool_;
  std::size_t      used_ = 0;
};

} // namespace nova::arch::stage1
