#pragma once

// components/core_mmu/include/stage2_descriptor.hpp
//
// Stage 2 page table descriptor encoding for ARMv8-A, 4KB granule.
// Header-only, constexpr — host-testable.
//
// Reference: ARM ARM DDI0487 §D5.3 (VMSAv8-64 Translation Table Format).
//
// Descriptor bit layout (64-bit):
//   [1:0]   type       00=Invalid, 01=Block (L1/L2), 11=Table (L0/L1/L2) or Page (L3)
//   [11:2]  lower attrs (Stage 2 encoding — distinct from Stage 1 MAIR-indexed form)
//     [5:2]   MemAttr[3:0]   outer [3:2] / inner [1:0]
//     [7:6]   S2AP           00 none, 01 RO, 10 WO, 11 RW
//     [9:8]   SH             00 none, 10 outer-sh, 11 inner-sh
//     [10]    AF             access flag
//   [47:12] output_addr (4 KiB-aligned frame address)
//   [53:52] contiguous hint
//   [54]    XN (execute-never)
//
// Stage 2 does NOT index through MAIR_EL1; MemAttr[3:0] encodes the
// memory type directly. Normal WB inner+outer cacheable = 0xF,
// Device-nGnRE = 0x1.

#include <cstdint>

namespace nova::mmu {

namespace desc {

// --- Type field (bits 1:0) ---------------------------------------------------

inline constexpr std::uint64_t kTypeMask    = 0b11ULL;
inline constexpr std::uint64_t kTypeInvalid = 0b00ULL;
inline constexpr std::uint64_t kTypeBlock   = 0b01ULL; // valid only at L1, L2
inline constexpr std::uint64_t kTypeTable   = 0b11ULL; // valid only at L0, L1, L2
inline constexpr std::uint64_t kTypePage    = 0b11ULL; // valid only at L3

// --- Lower attributes (bits 11:2) --- Stage 2 specific ---

inline constexpr std::uint64_t kMemAttrShift        = 2;
inline constexpr std::uint64_t kMemAttrMask         = 0xFULL << kMemAttrShift;
inline constexpr std::uint64_t kMemAttrNormalWB     = 0xFULL; // Outer+Inner WB cacheable RA WA
inline constexpr std::uint64_t kMemAttrDevice_nGnRE = 0x1ULL;

inline constexpr std::uint64_t kS2apShift     = 6;
inline constexpr std::uint64_t kS2apMask      = 0b11ULL << kS2apShift;
inline constexpr std::uint64_t kS2apNone      = 0b00ULL;
inline constexpr std::uint64_t kS2apReadOnly  = 0b01ULL;
inline constexpr std::uint64_t kS2apWriteOnly = 0b10ULL;
inline constexpr std::uint64_t kS2apReadWrite = 0b11ULL;

inline constexpr std::uint64_t kShShift          = 8;
inline constexpr std::uint64_t kShMask           = 0b11ULL << kShShift;
inline constexpr std::uint64_t kShNonShareable   = 0b00ULL;
inline constexpr std::uint64_t kShOuterShareable = 0b10ULL;
inline constexpr std::uint64_t kShInnerShareable = 0b11ULL;

inline constexpr std::uint64_t kAfBit = 1ULL << 10;

// --- Output address (bits 47:12) --- 4 KiB-aligned --------------------------

inline constexpr std::uint64_t kOutputAddrMask = 0x0000'FFFF'FFFF'F000ULL;

// --- Upper attributes -------------------------------------------------------

inline constexpr std::uint64_t kContigBit = 1ULL << 52;
inline constexpr std::uint64_t kXnBit     = 1ULL << 54;

// --- Convenience presets (combined lower-attr bitfields) --------------------

// Normal memory, RW, Inner Shareable, cacheable, executable, AF=1.
// Guest-kernel RAM regions use this.
inline constexpr std::uint64_t kAttrNormalRwx =
    (kMemAttrNormalWB << kMemAttrShift) | (kS2apReadWrite << kS2apShift) | (kShInnerShareable << kShShift) | kAfBit;

// Normal memory, RW, Inner Shareable, cacheable, non-executable (data).
inline constexpr std::uint64_t kAttrNormalRwData = kAttrNormalRwx | kXnBit;

// Device-nGnRE, RW, non-executable. MMIO regions (Phase 8+ when we expose
// emulated devices to guests).
inline constexpr std::uint64_t kAttrDeviceRw = (kMemAttrDevice_nGnRE << kMemAttrShift) |
                                               (kS2apReadWrite << kS2apShift) | (kShOuterShareable << kShShift) |
                                               kAfBit | kXnBit;

} // namespace desc

// --- Builders ---------------------------------------------------------------
//
// These helpers build descriptor values; they do not write to memory. A
// builder pass (Phase 5 next step) uses them to populate L1/L2/L3 tables.

// Block descriptor, valid at L1 (1 GiB) or L2 (2 MiB). The caller is
// responsible for ensuring output_pa is block-aligned for the level.
[[nodiscard]] constexpr std::uint64_t make_block(std::uint64_t output_pa, std::uint64_t attrs) noexcept {
  return (output_pa & desc::kOutputAddrMask) | attrs | desc::kTypeBlock;
}

// Page descriptor, valid only at L3 (4 KiB). output_pa must be 4KiB-aligned.
[[nodiscard]] constexpr std::uint64_t make_page(std::uint64_t output_pa, std::uint64_t attrs) noexcept {
  return (output_pa & desc::kOutputAddrMask) | attrs | desc::kTypePage;
}

// Table descriptor, valid at L0/L1/L2. next_table_pa is the PA of the
// next-level table (must be 4KiB-aligned). Stage 2 table descriptors
// carry no additional attribute bits.
[[nodiscard]] constexpr std::uint64_t make_table(std::uint64_t next_table_pa) noexcept {
  return (next_table_pa & desc::kOutputAddrMask) | desc::kTypeTable;
}

inline constexpr std::uint64_t kInvalid = 0;

// --- Field extraction (for tests and future invalidation logic) -------------

[[nodiscard]] constexpr std::uint64_t descriptor_type(std::uint64_t d) noexcept {
  return d & desc::kTypeMask;
}

[[nodiscard]] constexpr bool is_valid(std::uint64_t d) noexcept {
  return descriptor_type(d) != desc::kTypeInvalid;
}

[[nodiscard]] constexpr std::uint64_t output_addr(std::uint64_t d) noexcept {
  return d & desc::kOutputAddrMask;
}

[[nodiscard]] constexpr std::uint64_t mem_attr(std::uint64_t d) noexcept {
  return (d & desc::kMemAttrMask) >> desc::kMemAttrShift;
}

[[nodiscard]] constexpr std::uint64_t s2ap(std::uint64_t d) noexcept {
  return (d & desc::kS2apMask) >> desc::kS2apShift;
}

[[nodiscard]] constexpr std::uint64_t shareability(std::uint64_t d) noexcept {
  return (d & desc::kShMask) >> desc::kShShift;
}

[[nodiscard]] constexpr bool access_flag(std::uint64_t d) noexcept {
  return (d & desc::kAfBit) != 0;
}

[[nodiscard]] constexpr bool execute_never(std::uint64_t d) noexcept {
  return (d & desc::kXnBit) != 0;
}

} // namespace nova::mmu
