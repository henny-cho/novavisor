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

} // namespace nova::watchdog
