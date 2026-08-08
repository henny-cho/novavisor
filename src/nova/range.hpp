#pragma once

// Overflow-safe range predicates — the single definitions shared by the
// ABI validators (payload layout, DMA ownership policy) and their
// consumers. A range is [base, base + size); size 0 never overlaps.

#include <cstdint>
#include <limits>

namespace nova {

[[nodiscard]] constexpr auto range_well_formed(std::uint64_t base, std::uint64_t size) noexcept -> bool {
  return size != 0 && base <= std::numeric_limits<std::uint64_t>::max() - (size - 1U);
}

[[nodiscard]] constexpr auto ranges_overlap(std::uint64_t lhs_base, std::uint64_t lhs_size, std::uint64_t rhs_base,
                                            std::uint64_t rhs_size) noexcept -> bool {
  if (lhs_size == 0 || rhs_size == 0) {
    return false;
  }
  return lhs_base <= rhs_base ? rhs_base - lhs_base < lhs_size : lhs_base - rhs_base < rhs_size;
}

// True when [sub, sub + len) lies fully inside [base, base + size).
// Total in len: a request longer than the range has no address that
// satisfies it, so an overlong length is refused instead of wrapping the
// end-of-range bound below base. Callers therefore need no length clamp
// to ask the question safely.
[[nodiscard]] constexpr auto range_contains(std::uint64_t base, std::uint64_t size, std::uint64_t sub,
                                            std::uint64_t len) noexcept -> bool {
  return len <= size && sub >= base && sub - base <= size - len;
}

} // namespace nova
