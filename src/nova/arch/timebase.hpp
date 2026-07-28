#pragma once

// nova/arch/timebase.hpp
//
// Boot-time timebase contract plus the millisecond conversion every
// bounded wait derives from. CNTFRQ_EL0 is programmed by firmware at the
// highest exception level and the architecture fixes no value, so EL2
// has to judge what it reads instead of trusting it: an unprogrammed
// register reads zero, and every deadline computed from zero collapses
// to "now" — the time slice expires the instant it is armed, the
// secondary online wait times out before a core can answer, and every
// quiesce budget fires immediately. QEMU always reports a sane
// frequency, so nothing surfaces there.
//
// Pure and host-testable; the raw register value comes in through the
// hal facade.

#include <cstdint>
#include <limits>
#include <string_view>

namespace nova::arch {

// The usable window. Arm recommends a 1–50 MHz system counter; the
// bounds are deliberately wider so legitimate outlier silicon is not
// refused, while a value this far outside means the register was never
// correctly programmed — the same firmware bug as zero, just less
// obvious. Both fail loud rather than run with collapsed deadlines.
inline constexpr std::uint64_t kMinCounterHz = 1'000'000;
inline constexpr std::uint64_t kMaxCounterHz = 1'000'000'000;

enum class TimebaseError : std::uint8_t {
  kNone,
  kUnprogrammed, // CNTFRQ_EL0 reads zero — firmware never wrote it
  kOutOfRange,   // outside the usable window, so not a real counter rate
};

[[nodiscard]] constexpr auto validate_timebase(std::uint64_t hz) noexcept -> TimebaseError {
  if (hz == 0) {
    return TimebaseError::kUnprogrammed;
  }
  if (hz < kMinCounterHz || hz > kMaxCounterHz) {
    return TimebaseError::kOutOfRange;
  }
  return TimebaseError::kNone;
}

[[nodiscard]] constexpr auto to_string(TimebaseError error) noexcept -> std::string_view {
  switch (error) {
  case TimebaseError::kUnprogrammed:
    return "CNTFRQ_EL0 is zero (firmware did not program the timebase)";
  case TimebaseError::kOutOfRange:
    return "CNTFRQ_EL0 outside the usable counter range";
  case TimebaseError::kNone:
    return "ok";
  }
  return "unknown";
}

struct TickPlan {
  bool          accepted = false;
  std::uint64_t ticks    = 0;
};

struct DeadlinePlan {
  bool          accepted = false;
  std::uint64_t deadline = 0;
};

// Milliseconds → counter ticks without overflowing hz * ms. Splitting
// whole seconds from the remainder keeps sub-millisecond resolution for
// short windows while leaving long ones exact; a rejected plan means the
// caller must not arm anything (there is no safe fallback deadline).
[[nodiscard]] constexpr auto ms_to_ticks(std::uint64_t hz, std::uint64_t ms) noexcept -> TickPlan {
  if (hz == 0) {
    return {};
  }

  constexpr std::uint64_t kMillisPerSecond = 1000;
  constexpr std::uint64_t kMax             = std::numeric_limits<std::uint64_t>::max();
  const std::uint64_t     seconds          = ms / kMillisPerSecond;
  const std::uint64_t     millis           = ms % kMillisPerSecond;
  if (seconds > kMax / hz) {
    return {};
  }

  const std::uint64_t whole = seconds * hz;
  const std::uint64_t fraction =
      (hz / kMillisPerSecond) * millis + ((hz % kMillisPerSecond) * millis) / kMillisPerSecond;
  if (whole > kMax - fraction) {
    return {};
  }
  return {.accepted = true, .ticks = whole + fraction};
}

// Microseconds → counter ticks. Same split as above, one scale down: the
// validated window guarantees hz >= 1 MHz, so the whole-microsecond term
// never rounds a positive request to zero ticks.
[[nodiscard]] constexpr auto us_to_ticks(std::uint64_t hz, std::uint64_t us) noexcept -> TickPlan {
  if (hz == 0) {
    return {};
  }

  constexpr std::uint64_t kMicrosPerSecond = 1'000'000;
  constexpr std::uint64_t kMax             = std::numeric_limits<std::uint64_t>::max();
  const std::uint64_t     seconds          = us / kMicrosPerSecond;
  const std::uint64_t     micros           = us % kMicrosPerSecond;
  if (seconds > kMax / hz) {
    return {};
  }

  const std::uint64_t whole = seconds * hz;
  const std::uint64_t fraction =
      (hz / kMicrosPerSecond) * micros + ((hz % kMicrosPerSecond) * micros) / kMicrosPerSecond;
  if (whole > kMax - fraction) {
    return {};
  }
  return {.accepted = true, .ticks = whole + fraction};
}

// Absolute counter value `ms` from `now`, or a rejected plan when the
// conversion or the addition would wrap.
[[nodiscard]] constexpr auto deadline_after_ms(std::uint64_t now, std::uint64_t hz, std::uint64_t ms) noexcept
    -> DeadlinePlan {
  const TickPlan plan = ms_to_ticks(hz, ms);
  if (!plan.accepted || now > std::numeric_limits<std::uint64_t>::max() - plan.ticks) {
    return {};
  }
  return {.accepted = true, .deadline = now + plan.ticks};
}

} // namespace nova::arch
