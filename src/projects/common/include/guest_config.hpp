#pragma once

// Guest table construction shared by AArch64 board profiles.

#include "nova/abi/guest.hpp"
#include "nova/abi/guest_layout.h"

#include <cstddef>
#include <cstdint>

namespace nova::project {

// Guest IPA window, from the layout header shared with the demo guest
// linker script (demo/common/linker.ld.S). Every guest sees (and links
// against) the same window; only the backing PA slot differs.
inline constexpr std::uint64_t kGuestIpaBase = NOVA_GUEST_IPA_BASE;
inline constexpr std::uint64_t kGuestIpaSize = NOVA_GUEST_IPA_SIZE;

// EL1 entry PC. The demo's linker.ld places .text.start at IPA base.
inline constexpr std::uint64_t kGuestEntry = kGuestIpaBase;

// Per-guest DTB IPA and initial SP_EL1: the DTB reservation sits at the
// configured window top and the stack grows down from it. For the
// minimum window this equals the link-time __stack_top demo/common/
// linker.ld.S derives from the same macros.
[[nodiscard]] constexpr auto guest_dtb_ipa(std::uint64_t mem_size) noexcept -> std::uint64_t {
  return kGuestIpaBase + mem_size - NOVA_GUEST_DTB_SIZE;
}

// Builds the runtime table once on the boot core before RuntimeStart.
void init_guest_table() noexcept;

} // namespace nova::project
