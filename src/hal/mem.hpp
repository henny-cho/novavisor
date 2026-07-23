#pragma once

#include <cstddef>

namespace nova::memory {

struct RestoreStats {
  std::size_t examined_bytes = 0;
  std::size_t written_bytes  = 0;
};

// Restore an exact pristine image while skipping stores for unchanged
// aligned blocks. Source and destination must not overlap.
[[nodiscard]] auto restore_changed(void* destination, const void* pristine, std::size_t size) noexcept -> RestoreStats;

} // namespace nova::memory
