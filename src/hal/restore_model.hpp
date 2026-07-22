#pragma once

#include "hal/mem.hpp"

#include <cstddef>
#include <cstdint>
#include <span>

namespace nova::memory {

// Host-testable model of the 16-byte restore granularity used by the
// AArch64 implementation. A changed word restores its whole block.
template <std::size_t WordsPerBlock = 2>
[[nodiscard]] constexpr auto restore_changed_words(std::span<std::uint64_t>       destination,
                                                   std::span<const std::uint64_t> pristine) noexcept -> RestoreStats {
  static_assert(WordsPerBlock > 0);
  const std::size_t words = destination.size() < pristine.size() ? destination.size() : pristine.size();
  RestoreStats      stats{.examined_bytes = words * sizeof(std::uint64_t), .written_bytes = 0};

  std::size_t offset = 0;
  while (offset + WordsPerBlock <= words) {
    bool changed = false;
    for (std::size_t i = 0; i < WordsPerBlock; ++i) {
      changed = changed || destination[offset + i] != pristine[offset + i];
    }
    if (changed) {
      for (std::size_t i = 0; i < WordsPerBlock; ++i) {
        destination[offset + i] = pristine[offset + i];
      }
      stats.written_bytes += WordsPerBlock * sizeof(std::uint64_t);
    }
    offset += WordsPerBlock;
  }

  while (offset < words) {
    if (destination[offset] != pristine[offset]) {
      destination[offset] = pristine[offset];
      stats.written_bytes += sizeof(std::uint64_t);
    }
    ++offset;
  }
  return stats;
}

} // namespace nova::memory
