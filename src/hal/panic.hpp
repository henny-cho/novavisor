#pragma once

// hal/panic.hpp
//
// First-failure panic protocol, and the one way a fatal EL2 path ends.
// The first claimant owns the console raw, other cores' output is
// dropped, and a stop SGI parks them — one attributable report per log.
// fail() is that sequence; the host verifier reads kPrefix from here.

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

inline constexpr std::string_view kPrefix = "[NOVA PANIC] ";

// Claim the machine and emit the headline. Returns only to the first
// claimant, so a path with more to say (a register dump) adds it and
// then halts; everything else ends in fail().
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
