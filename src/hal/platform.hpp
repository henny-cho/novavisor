#pragma once

// hal/platform.hpp
//
// Build identity facade: which board and which composition this image
// is. The boot report needs both, and generic code may not name a board
// (tools/check_platform_boundaries.py rejects board names in the
// reusable trees) — so the board string comes from the board tree and
// the profile name from the build system, and this is the one place
// components read either.

#include "hal/board/active/board.hpp"

#include <string_view>

namespace nova::platform {

inline constexpr std::string_view kBoardName = board::active::kName;

#ifdef NOVA_PROFILE_NAME
inline constexpr std::string_view kProfileName = NOVA_PROFILE_NAME;
#else
inline constexpr std::string_view kProfileName = "unknown";
#endif

} // namespace nova::platform
