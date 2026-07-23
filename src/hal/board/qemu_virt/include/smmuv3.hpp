#pragma once

#include "board.hpp"

#include <cstdint>

namespace nova::board::qemu_virt::smmuv3 {

[[nodiscard]] inline auto read32(std::uint32_t offset) noexcept -> std::uint32_t {
  return *reinterpret_cast<volatile std::uint32_t*>(SMMU_BASE + offset);
}

inline void write32(std::uint32_t offset, std::uint32_t value) noexcept {
  *reinterpret_cast<volatile std::uint32_t*>(SMMU_BASE + offset) = value;
}

inline void write64(std::uint32_t offset, std::uint64_t value) noexcept {
  *reinterpret_cast<volatile std::uint64_t*>(SMMU_BASE + offset) = value;
}

inline void publish_memory() noexcept {
  __asm__ volatile("dsb oshst" ::: "memory");
}

inline void acquire_memory() noexcept {
  __asm__ volatile("dmb oshld" ::: "memory");
}

} // namespace nova::board::qemu_virt::smmuv3
