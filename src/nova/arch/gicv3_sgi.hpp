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

} // namespace nova::arch::gicv3
