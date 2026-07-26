#pragma once

// nova/panic.hpp
//
// Foundation-level terminal stop. Lives in the pure tree so hal and
// component code can end a fatal path without depending on the
// nova_panic component (which owns the stdx panic handler and the
// libstdc++ assertion sink — both need the console and stay above).

#include <cstdint>

namespace nova {

// SGI a panicking core broadcasts so every other PE parks at its next
// trap instead of writing over the first failure's report. Lives here
// (not hal/panic.hpp) so hal/gic.hpp can enable and match it without an
// include cycle.
inline constexpr std::uint32_t kPanicStopSgi = 15;

// Unconditionally halt the CPU. Used as the terminal action in all
// panic paths.
[[noreturn]] inline void halt() noexcept {
  while (true) {
#if defined(__aarch64__)
    asm volatile("wfi");
#endif
  }
}

} // namespace nova
