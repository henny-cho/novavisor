#pragma once

// hal/panic.hpp
//
// First-failure panic protocol. A fatal EL2 path claims the machine
// before reporting: the first claimant owns the console raw (the
// normal lock may be held by a core that will never release it),
// every other core's console output is dropped, and a stop SGI asks
// the remaining PEs to park at their next trap — so the serial log
// ends with exactly one attributable failure report instead of an
// interleaved stream of watchdog resets and guest output.

#include "hal/console.hpp"
#include "hal/cpu.hpp"
#include "hal/gic.hpp"
#include "nova/panic.hpp"

#include <atomic>
#include <cstddef>
#include <cstdint>

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

} // namespace nova::panic
