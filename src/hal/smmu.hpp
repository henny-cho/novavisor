#pragma once

// Board-neutral access to the active SMMUv3 register frame.

#include "hal/board/active/smmuv3.hpp"

#include <cstdint>

namespace nova::smmu::hw {

inline constexpr std::uint32_t kEventIntid   = board::active::kSmmuEventIntid;
inline constexpr std::uint32_t kCommandIntid = board::active::kSmmuCommandIntid;
inline constexpr std::uint32_t kErrorIntid   = board::active::kSmmuErrorIntid;

[[nodiscard]] inline auto read32(std::uint32_t offset) noexcept -> std::uint32_t {
  return board::active::smmuv3::read32(offset);
}

inline void write32(std::uint32_t offset, std::uint32_t value) noexcept {
  board::active::smmuv3::write32(offset, value);
}

inline void write64(std::uint32_t offset, std::uint64_t value) noexcept {
  board::active::smmuv3::write64(offset, value);
}

inline void publish_memory() noexcept {
  board::active::smmuv3::publish_memory();
}

inline void acquire_memory() noexcept {
  board::active::smmuv3::acquire_memory();
}

} // namespace nova::smmu::hw
