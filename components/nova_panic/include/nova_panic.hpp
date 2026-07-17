#pragma once

#include "hal/console.hpp"

#include <stdx/ct_string.hpp>
#include <string_view>

namespace nova {

// Unconditionally halt the CPU. Used as the terminal action in all panic paths.
[[noreturn]] inline void halt() noexcept {
  while (true) {
    asm volatile("wfi");
  }
}

// Custom stdx panic handler for bare-metal.
// On any CIB assertion failure (e.g. calling an uninitialized service),
// prints a console message then halts the CPU.
struct NovaPanicHandler {
  template <typename... Args>
  static auto panic(Args&&... /*args*/) noexcept -> void {
    console::write("[NOVA PANIC] System halted.\n");
    halt();
  }

  template <stdx::ct_string S, typename... Args>
  static auto panic(Args&&... /*args*/) noexcept -> void {
    using namespace std::string_view_literals;
    console::write("[NOVA PANIC] "sv);
    console::write(std::string_view{S.data(), S.size()});
    console::write("\n[NOVA PANIC] System halted.\n"sv);
    halt();
  }
};

} // namespace nova
