#pragma once

// nova/panic.hpp
//
// Foundation-level terminal stop. Lives in the pure tree so hal and
// component code can end a fatal path without depending on the
// nova_panic component (which owns the stdx panic handler and the
// libstdc++ assertion sink — both need the console and stay above).

namespace nova {

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
