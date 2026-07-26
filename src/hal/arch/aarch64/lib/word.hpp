#pragma once

// hal/arch/aarch64/lib/word.hpp
//
// Word-alignment predicates shared by the strict-alignment libc
// routines and the pristine-restore loop. Both run with the EL2 MMU
// off, where every access is Device memory and must be size-aligned,
// so both gate their wide paths on the same test.

#include <cstdint>

namespace nova::arch::word {

inline constexpr std::uintptr_t kMask     = sizeof(std::uint64_t) - 1;
inline constexpr std::uintptr_t kPairMask = (2 * sizeof(std::uint64_t)) - 1;

[[nodiscard]] inline auto misalign(const void* p) noexcept -> std::uintptr_t {
  return reinterpret_cast<std::uintptr_t>(p) & kMask;
}

} // namespace nova::arch::word
