#pragma once

// PCI requester identity and enhanced configuration address layout.

#include <cstdint>

namespace nova::arch::pci {

struct Bdf {
  std::uint8_t bus      = 0;
  std::uint8_t device   = 0;
  std::uint8_t function = 0;
};

[[nodiscard]] constexpr auto valid(Bdf bdf) noexcept -> bool {
  return bdf.device < 32U && bdf.function < 8U;
}

[[nodiscard]] constexpr auto requester_id(Bdf bdf) noexcept -> std::uint16_t {
  return valid(bdf) ? static_cast<std::uint16_t>((static_cast<std::uint16_t>(bdf.bus) << 8U) |
                                                 (static_cast<std::uint16_t>(bdf.device) << 3U) | bdf.function)
                    : 0;
}

[[nodiscard]] constexpr auto ecam_offset(Bdf bdf, std::uint16_t register_offset) noexcept -> std::uint64_t {
  if (!valid(bdf) || register_offset >= 4096U) {
    return 0;
  }
  return (static_cast<std::uint64_t>(bdf.bus) << 20U) | (static_cast<std::uint64_t>(bdf.device) << 15U) |
         (static_cast<std::uint64_t>(bdf.function) << 12U) | register_offset;
}

} // namespace nova::arch::pci
