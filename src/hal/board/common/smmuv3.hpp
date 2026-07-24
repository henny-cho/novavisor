#pragma once

#include <cstdint>

namespace nova::board::common {

template <std::uintptr_t Base>
struct Smmuv3 {
  [[nodiscard]] static auto read32(std::uint32_t offset) noexcept -> std::uint32_t {
    return *reinterpret_cast<volatile std::uint32_t*>(Base + offset);
  }

  static void write32(std::uint32_t offset, std::uint32_t value) noexcept {
    *reinterpret_cast<volatile std::uint32_t*>(Base + offset) = value;
  }

  static void write64(std::uint32_t offset, std::uint64_t value) noexcept {
    *reinterpret_cast<volatile std::uint64_t*>(Base + offset) = value;
  }

  static void publish_memory() noexcept { __asm__ volatile("dsb oshst" ::: "memory"); }

  static void acquire_memory() noexcept { __asm__ volatile("dmb oshld" ::: "memory"); }
};

} // namespace nova::board::common
