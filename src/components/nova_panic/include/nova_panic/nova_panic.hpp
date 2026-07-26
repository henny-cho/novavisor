#pragma once

#include "hal/console.hpp"
#include "hal/panic.hpp"
#include "nova/panic.hpp"

#include <stdx/ct_string.hpp>
#include <string_view>

namespace nova {

// Custom stdx panic handler for bare-metal.
// On any CIB assertion failure (e.g. calling an uninitialized service),
// claims the first-failure report (raw console, neighbors parked),
// prints the message, then halts the CPU.
struct NovaPanicHandler {
  // Shared preamble: claim the machine; bystanders and a re-faulting
  // owner park without burying the first report.
  static auto claim() noexcept -> bool {
    switch (panic::enter()) {
    case panic::Role::kRecursive:
      console::write("\n[NOVA PANIC] recursive panic\n");
      halt();
    case panic::Role::kBystander:
      halt();
    case panic::Role::kFirst:
      return true;
    }
    return true;
  }

  template <typename... Args>
  static auto panic(Args&&... /*args*/) noexcept -> void {
    claim();
    console::write("[NOVA PANIC] System halted.\n");
    halt();
  }

  template <stdx::ct_string S, typename... Args>
  static auto panic(Args&&... /*args*/) noexcept -> void {
    using namespace std::string_view_literals;
    claim();
    console::write("[NOVA PANIC] "sv);
    console::write(std::string_view{S.data(), S.size()});
    console::write("\n[NOVA PANIC] System halted.\n"sv);
    halt();
  }
};

} // namespace nova
