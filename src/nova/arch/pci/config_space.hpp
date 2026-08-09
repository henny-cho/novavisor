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

// The identity a function presents to the SMMU and the frame its
// configuration lives in are the same three numbers packed two ways.
// A BDF outside the addressable fields names neither.
static_assert(
    [] {
      const Bdf dev = {.bus = 0, .device = 2, .function = 0};
      const Bdf top = {.bus = 0xFF, .device = 31, .function = 7};
      return requester_id(dev) == 0x0010U &&                    // bus:device:function packed 8:5:3
             requester_id(top) == 0xFFFFU &&                    // the widest identity still fits 16 bits
             ecam_offset(dev, 0x10) == 0x1'0010U &&             // device stride 32 KiB, register byte-addressed
             ecam_offset({.bus = 1}, 0) == 0x10'0000U &&        // bus stride 1 MiB
             ecam_offset({.function = 1}, 0) == 0x1000U &&      // one 4 KiB frame per function
             !valid({.device = 32}) && !valid({.function = 8}); // 5-bit device field, 3-bit function field
    }(),
    "a BDF names one requester and one 4 KiB configuration frame");

// An unaddressable BDF, or a register past the frame, must not fold
// into some other function's identity or some other function's frame.
static_assert(
    [] {
      return requester_id({.device = 32}) == 0 && ecam_offset({.device = 32}, 0) == 0 &&
             ecam_offset({.function = 8}, 0) == 0 &&          //
             ecam_offset({.bus = 0, .device = 2}, 4096) == 0; // the register field is 12 bits wide
    }(),
    "an address this cannot form is refused rather than aliased");

} // namespace nova::arch::pci
