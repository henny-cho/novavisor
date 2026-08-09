#pragma once

// Stage-2-only stream table entry encoding for the initial DMA path.
// Stage-1 context descriptors are intentionally outside this encoder.

#include "core_mmu/stage2_descriptor.hpp"

#include <array>
#include <cstddef>
#include <cstdint>

namespace nova::smmu {

using StreamTableEntry = std::array<std::uint64_t, 8>;

inline constexpr std::size_t kStreamTableEntryBytes = 64;

namespace ste {

inline constexpr std::uint64_t kValid       = 1ULL << 0U;
inline constexpr std::uint64_t kConfigShift = 1;
inline constexpr std::uint64_t kConfigMask  = 0b111ULL << kConfigShift;
inline constexpr std::uint64_t kStage2Only  = 0b110ULL;

inline constexpr std::uint64_t kVmidMask           = 0xFFFFULL;
inline constexpr std::uint64_t kT0szShift          = 32;
inline constexpr std::uint64_t kSl0Shift           = 38;
inline constexpr std::uint64_t kIrgn0Shift         = 40;
inline constexpr std::uint64_t kOrgn0Shift         = 42;
inline constexpr std::uint64_t kSh0Shift           = 44;
inline constexpr std::uint64_t kTg0Shift           = 46;
inline constexpr std::uint64_t kPsShift            = 48;
inline constexpr std::uint64_t kAa64               = 1ULL << 51U;
inline constexpr std::uint64_t kProtectedTableWalk = 1ULL << 54U;
inline constexpr std::uint64_t kRecordFault        = 1ULL << 58U;
inline constexpr std::uint64_t kS2ttbMask          = 0x000F'FFFF'FFFF'FFF0ULL;
inline constexpr std::uint64_t kPa40Mask           = 0x0000'00FF'FFFF'FFFFULL;

// Walk geometry comes from the shared Stage 2 definition: the SMMU
// walks the same tables the CPU's VTCR_EL2 describes.
inline constexpr std::uint64_t kT0sz           = mmu::kStage2T0sz;
inline constexpr std::uint64_t kSl0            = mmu::kStage2Sl0;
inline constexpr std::uint64_t kWriteBack      = 0b11;
inline constexpr std::uint64_t kInnerShareable = 0b11;
inline constexpr std::uint64_t kGranule4k      = mmu::kStage2Granule4k;
inline constexpr std::uint64_t kPhysicalSize40 = mmu::kStage2Pa40;

} // namespace ste

enum class SteError : std::uint8_t {
  kNone,
  kUnalignedRoot,
  kRootOutOfRange,
};

struct SteEncoding {
  StreamTableEntry entry{};
  SteError         error = SteError::kNone;

  [[nodiscard]] constexpr auto ok() const noexcept -> bool { return error == SteError::kNone; }
};

[[nodiscard]] constexpr auto make_abort_ste() noexcept -> StreamTableEntry {
  StreamTableEntry entry{};
  entry[0] = ste::kValid;
  return entry;
}

// The root must belong to a DMA-only table set, not a CPU table with shared mappings.
[[nodiscard]] constexpr auto make_stage2_ste(std::uint64_t root_pa, std::uint16_t vmid) noexcept -> SteEncoding {
  if ((root_pa & 0xFFFU) != 0U) {
    return {.error = SteError::kUnalignedRoot};
  }
  if ((root_pa & ~ste::kPa40Mask) != 0U) {
    return {.error = SteError::kRootOutOfRange};
  }

  StreamTableEntry entry{};
  entry[0] = ste::kValid | (ste::kStage2Only << ste::kConfigShift);
  entry[2] = (static_cast<std::uint64_t>(vmid) & ste::kVmidMask) | (ste::kT0sz << ste::kT0szShift) |
             (ste::kSl0 << ste::kSl0Shift) | (ste::kWriteBack << ste::kIrgn0Shift) |
             (ste::kWriteBack << ste::kOrgn0Shift) | (ste::kInnerShareable << ste::kSh0Shift) |
             (ste::kGranule4k << ste::kTg0Shift) | (ste::kPhysicalSize40 << ste::kPsShift) | ste::kAa64 |
             ste::kProtectedTableWalk | ste::kRecordFault;
  entry[3] = root_pa & ste::kS2ttbMask;
  return {.entry = entry};
}

static_assert(sizeof(StreamTableEntry) == kStreamTableEntryBytes,
              "a stream table entry is the 64-byte record the SMMU indexes by stream ID");

// The entry a configured stream gets, decoded field by field. The
// geometry fields are the load-bearing ones: the SMMU walks the very
// tables VTCR_EL2 describes, so they are read from the Stage 2
// definition and must still be there after the shifts are applied.
static_assert(
    [] {
      const std::uint64_t root    = 0x0000'0000'1234'5000;
      const std::uint16_t vmid    = 0x1234;
      const SteEncoding   ste_out = make_stage2_ste(root, vmid);
      const std::uint64_t cfg     = ste_out.entry[2];
      return ste_out.ok() && ste_out.entry[0] == 0xDULL && // V = 1, Config = 0b110 (Stage 2 only)
             (cfg & ste::kVmidMask) == vmid && ((cfg >> ste::kT0szShift) & 0x3FULL) == ste::kT0sz &&
             ((cfg >> ste::kSl0Shift) & 0x3ULL) == ste::kSl0 &&
             ((cfg >> ste::kIrgn0Shift) & 0x3ULL) == ste::kWriteBack &&
             ((cfg >> ste::kOrgn0Shift) & 0x3ULL) == ste::kWriteBack &&
             ((cfg >> ste::kSh0Shift) & 0x3ULL) == ste::kInnerShareable &&
             ((cfg >> ste::kTg0Shift) & 0x3ULL) == ste::kGranule4k &&
             ((cfg >> ste::kPsShift) & 0x7ULL) == ste::kPhysicalSize40 && (cfg & ste::kAa64) != 0U &&
             (cfg & ste::kProtectedTableWalk) != 0U && // a device walk may not reach hypervisor memory
             (cfg & ste::kRecordFault) != 0U &&        // a refused transaction leaves a record behind
             ste_out.entry[3] == root && ste_out.entry[1] == 0U && ste_out.entry[4] == 0U && ste_out.entry[5] == 0U &&
             ste_out.entry[6] == 0U && ste_out.entry[7] == 0U; // no stage-1 context descriptor
    }(),
    "a configured stream translates through its VM's Stage 2 with the CPU's own walk geometry");

// A root the entry cannot encode is refused rather than truncated: a
// silently masked root would point the device at whatever table happens
// to live at the truncated address.
static_assert(make_stage2_ste(0x1234, 1).error == SteError::kUnalignedRoot &&
                  make_stage2_ste(1ULL << 40U, 1).error == SteError::kRootOutOfRange &&
                  make_abort_ste()[0] == ste::kValid, // valid with Config = 0: every transaction refused
              "an unencodable root is refused, and the abort entry translates nothing");

} // namespace nova::smmu
