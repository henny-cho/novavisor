#pragma once

// hal/panic.hpp
//
// First-failure panic protocol, and the one way a fatal EL2 path ends.
// A fatal path claims the machine before reporting: the first claimant
// owns the console raw (the normal lock may be held by a core that will
// never release it), every other core's console output is dropped, and
// a stop SGI asks the remaining PEs to park at their next trap — so the
// serial log ends with exactly one attributable failure report instead
// of an interleaved stream of watchdog resets and guest output.
//
// fail() is that whole sequence, so no caller writes the report prefix
// or the claim itself. The prefix is defined once here because a host
// verifier reads it from this header to know what a failed run looks
// like: a second spelling anywhere would be a guard that stops matching
// without anything failing to build.

#include "hal/console.hpp"
#include "hal/cpu.hpp"
#include "hal/gic.hpp"
#include "nova/panic.hpp"

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <string_view>

namespace nova::panic {

enum class Role : std::uint8_t {
  kFirst,     // this core owns the failure report
  kRecursive, // the owner faulted again inside its own report path
  kBystander, // another core is reporting — park without output
};

inline auto enter() noexcept -> Role {
  const std::size_t me       = cpu::id();
  std::size_t       expected = console::kNoPanicOwner;
  if (console::g_panic_owner.compare_exchange_strong(expected, me, std::memory_order_acq_rel)) {
    gic::broadcast_panic_stop();
    return Role::kFirst;
  }
  return expected == me ? Role::kRecursive : Role::kBystander;
}

// The report line every fatal path starts with. Read by the host
// verifier out of this header; never spelled a second time.
inline constexpr std::string_view kPrefix = "[NOVA PANIC] ";

// Claim the machine and emit the headline. Returns only to the first
// claimant, so a path with more to say (a register dump) adds it and
// then halts; everything else ends in fail() instead.
template <typename... Parts>
inline void announce(Parts... parts) noexcept {
  switch (enter()) {
  case Role::kRecursive:
    console::line("\n", kPrefix, "recursive fault inside the panic path\n");
    halt();
  case Role::kBystander:
    halt(); // the first failure owns the log
  case Role::kFirst:
    break;
  }
  console::line("\n", kPrefix, parts..., "\n");
}

// A fatal path with nothing further to report.
template <typename... Parts>
[[noreturn]] inline void fail(Parts... parts) noexcept {
  announce(parts...);
  halt();
}

} // namespace nova::panic
