#pragma once

// Board-neutral access to the active SMMUv3 register frame.

#include "hal/board/qemu_virt/include/board_layout.h"
#include "hal/board/qemu_virt/include/smmuv3.hpp"

#include <cstdint>

namespace nova::smmu::hw {

inline constexpr std::uint32_t kEventIntid   = NOVA_BOARD_SMMU_EVENT_INTID;
inline constexpr std::uint32_t kCommandIntid = NOVA_BOARD_SMMU_CMD_INTID;
inline constexpr std::uint32_t kErrorIntid   = NOVA_BOARD_SMMU_ERROR_INTID;

[[nodiscard]] inline auto read32(std::uint32_t offset) noexcept -> std::uint32_t {
  return board::qemu_virt::smmuv3::read32(offset);
}

inline void write32(std::uint32_t offset, std::uint32_t value) noexcept {
  board::qemu_virt::smmuv3::write32(offset, value);
}

inline void write64(std::uint32_t offset, std::uint64_t value) noexcept {
  board::qemu_virt::smmuv3::write64(offset, value);
}

inline void publish_memory() noexcept {
  board::qemu_virt::smmuv3::publish_memory();
}

inline void acquire_memory() noexcept {
  board::qemu_virt::smmuv3::acquire_memory();
}

} // namespace nova::smmu::hw
