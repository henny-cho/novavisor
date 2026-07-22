#pragma once

// Pure watchdog deadline and generation policy, host-testable.

#include <cstdint>
#include <limits>

namespace nova::watchdog {

struct DeadlinePlan {
  bool          accepted = false;
  std::uint64_t deadline = 0;
};

// Convert milliseconds without overflowing freq * window_ms. A zero
// window is the explicit disarm token and therefore keeps deadline 0.
[[nodiscard]] constexpr auto deadline_after_ms(std::uint64_t now, std::uint64_t freq, std::uint64_t window_ms) noexcept
    -> DeadlinePlan {
  if (window_ms == 0) {
    return {.accepted = true, .deadline = 0};
  }
  if (freq == 0) {
    return {};
  }

  constexpr std::uint64_t kMillisPerSecond = 1000;
  constexpr std::uint64_t kMax             = std::numeric_limits<std::uint64_t>::max();
  const std::uint64_t     seconds          = window_ms / kMillisPerSecond;
  const std::uint64_t     millis           = window_ms % kMillisPerSecond;
  if (seconds > kMax / freq) {
    return {};
  }

  const std::uint64_t whole = seconds * freq;
  const std::uint64_t fraction =
      (freq / kMillisPerSecond) * millis + ((freq % kMillisPerSecond) * millis) / kMillisPerSecond;
  if (whole > kMax - fraction) {
    return {};
  }
  const std::uint64_t ticks = whole + fraction;
  if (now > kMax - ticks) {
    return {};
  }
  return {.accepted = true, .deadline = now + ticks};
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
