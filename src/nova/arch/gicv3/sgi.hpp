#pragma once

#include <cstdint>

namespace nova::arch::gicv3 {

inline constexpr std::uint32_t kSgiCount = 16;

[[nodiscard]] constexpr auto sgi_target_supported(std::uint64_t affinity) noexcept -> bool {
  return (affinity & 0xFFU) < 16U;
}

// ICC_SGI1R_EL1 rearranges MPIDR affinity fields around INTID and the
// Aff0 target bitmap. Range-selector targeting is unnecessary for the
// supported boards because every Aff0 is below 16.
[[nodiscard]] constexpr auto sgi1r_value(std::uint64_t affinity, std::uint32_t intid) noexcept -> std::uint64_t {
  const std::uint64_t aff0 = affinity & 0xFFU;
  const std::uint64_t aff1 = (affinity >> 8U) & 0xFFU;
  const std::uint64_t aff2 = (affinity >> 16U) & 0xFFU;
  const std::uint64_t aff3 = (affinity >> 32U) & 0xFFU;
  return (aff3 << 48U) | (aff2 << 32U) | (static_cast<std::uint64_t>(intid) << 24U) | (aff1 << 16U) | (1ULL << aff0);
}

// An MPIDR affinity becomes exactly one target: the upper levels keep
// fields of their own, Aff0 becomes a bit in the target list.
static_assert(
    [] {
      return sgi1r_value(0x0, 5) == ((5ULL << 24U) | 0b01ULL) &&                      // Aff0 0 is target bit 0
             sgi1r_value(0x1, 5) == ((5ULL << 24U) | 0b10ULL) &&                      // Aff0 selects a bit, not a field
             sgi1r_value(0x0000'0100, 7) == ((7ULL << 24U) | (1ULL << 16U) | 1ULL) && // Aff1 at [23:16]
             sgi1r_value(0x0001'0000, 7) == ((1ULL << 32U) | (7ULL << 24U) | 1ULL) && // Aff2 at [39:32]
             sgi1r_value(0x01'0001'0102, 7) ==                                        // Aff3 at [55:48]
                 ((1ULL << 48U) | (1ULL << 32U) | (7ULL << 24U) | (1ULL << 16U) | (1ULL << 2U)) &&
             sgi_target_supported(0x0F) && // an Aff0 the 16-bit target list can name
             !sgi_target_supported(0x10);  // above it, targeting would need the range selector
    }(),
    "ICC_SGI1R_EL1 encodes one INTID to one affinity's target bit");

} // namespace nova::arch::gicv3
