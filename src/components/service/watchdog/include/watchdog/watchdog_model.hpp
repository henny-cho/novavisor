#pragma once

// Pure watchdog deadline and generation policy, host-testable.

#include "nova/arch/timebase.hpp"

#include <cstdint>

namespace nova::watchdog {

// The overflow-safe millisecond conversion this used to own now lives in
// nova/arch/timebase.hpp, where the nine other deadline sites can reach
// it. What stays here is the part that is watchdog policy rather than
// arithmetic: a zero window is the explicit disarm token.
using DeadlinePlan = arch::DeadlinePlan;

[[nodiscard]] constexpr auto deadline_after_ms(std::uint64_t now, std::uint64_t freq, std::uint64_t window_ms) noexcept
    -> DeadlinePlan {
  if (window_ms == 0) {
    return {.accepted = true, .deadline = 0};
  }
  return arch::deadline_after_ms(now, freq, window_ms);
}

[[nodiscard]] constexpr auto accepts_generation(std::uint64_t request, std::uint64_t current,
                                                bool boot_vcpu_on) noexcept -> bool {
  return request != 0 && request == current && boot_vcpu_on;
}

[[nodiscard]] constexpr auto accepts_update(std::uint64_t request_generation, std::uint64_t current_generation,
                                            std::uint64_t request_sequence, std::uint64_t current_sequence,
                                            bool boot_vcpu_on) noexcept -> bool {
  return accepts_generation(request_generation, current_generation, boot_vcpu_on) && request_sequence != 0 &&
         request_sequence == current_sequence;
}

// A zero window is the disarm token, not a deadline of zero ticks: it is
// accepted, and the deadline it plans is the one the caller compares
// against to mean "nothing armed".
static_assert(
    [] {
      const DeadlinePlan disarm = deadline_after_ms(123, 0, 0);
      return disarm.accepted && disarm.deadline == 0U;
    }(),
    "a zero window disarms the watchdog instead of expiring it immediately");

// What a petting request must carry to be honoured: the live boot
// generation, from a running boot vCPU, at the sequence the watchdog is
// waiting on. Generation zero is never live, so a request that arrives
// before the first arm — or after a reset invalidated it — is refused
// rather than silently extending someone else's deadline.
static_assert(
    [] {
      return accepts_generation(7, 7, true) && !accepts_generation(6, 7, true) &&         // a stale generation
             !accepts_generation(7, 7, false) &&                                          // boot vCPU is down
             !accepts_generation(0, 0, true) &&                                           // zero is never a generation
             accepts_update(7, 7, 12, 12, true) && !accepts_update(7, 7, 11, 12, true) && // superseded sequence
             !accepts_update(6, 7, 12, 12, true);
    }(),
    "only the current sequence of the live boot generation refreshes the watchdog");

} // namespace nova::watchdog
