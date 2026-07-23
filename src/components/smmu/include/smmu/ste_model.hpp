#pragma once

// Stage-2-only stream table entry encoding for the initial DMA path.
// Stage-1 context descriptors are intentionally outside this encoder.

#include <array>
#include <cstddef>
#include <cstdint>

namespace nova::smmu {

using StreamTableEntry = std::array<std::uint64_t, 8>;

inline constexpr std::size_t kStreamTableEntryBytes = 64;

namespace ste {

inline constexpr std::uint64_t kValid        = 1ULL << 0U;
inline constexpr std::uint64_t kConfigShift  = 1;
inline constexpr std::uint64_t kConfigMask   = 0b111ULL << kConfigShift;
inline constexpr std::uint64_t kStage1Enable = 0b001ULL;
inline constexpr std::uint64_t kStage1Only   = 0b101ULL;
inline constexpr std::uint64_t kStage2Only   = 0b110ULL;

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

inline constexpr std::uint64_t kT0sz           = 25;   // 39-bit IPA
inline constexpr std::uint64_t kSl0            = 0b01; // L1 root
inline constexpr std::uint64_t kWriteBack      = 0b11;
inline constexpr std::uint64_t kInnerShareable = 0b11;
inline constexpr std::uint64_t kGranule4k      = 0b00;
inline constexpr std::uint64_t kPhysicalSize40 = 0b010;

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

[[nodiscard]] constexpr auto config(const StreamTableEntry& entry) noexcept -> std::uint64_t {
  return (entry[0] & ste::kConfigMask) >> ste::kConfigShift;
}

[[nodiscard]] constexpr auto is_stage2_only(const StreamTableEntry& entry) noexcept -> bool {
  return (entry[0] & ste::kValid) != 0U && config(entry) == ste::kStage2Only;
}

[[nodiscard]] constexpr auto uses_context_descriptor(const StreamTableEntry& entry) noexcept -> bool {
  return (config(entry) & ste::kStage1Enable) != 0U;
}

} // namespace nova::smmu
