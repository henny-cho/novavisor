#pragma once

#include "hal/board/active/board.hpp"
#include "nova/mem_model.hpp"

#include <cstddef>
#include <cstdint>

namespace nova::memory {

// Reserved-region facts of the active board: the guest load window and
// the EL2-private IVC page / pristine-snapshot area behind it.
inline constexpr std::uint64_t kGuestPaBase     = board::active::kGuestPaBase;
inline constexpr std::uint64_t kGuestPaSize     = board::active::kGuestPaSize;
inline constexpr std::uint64_t kIvcShmPa        = board::active::kIvcShmPa;
inline constexpr std::uint64_t kGuestPristinePa = board::active::kGuestPristinePa;
inline constexpr std::uint64_t kPristineSize    = board::active::kPristineSize;

// Restore an exact pristine image while skipping stores for unchanged
// aligned blocks. Source and destination must not overlap.
[[nodiscard]] auto restore_changed(void* destination, const void* pristine, std::size_t size) noexcept -> RestoreStats;

} // namespace nova::memory
