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

// The verdict at the edges that decide it, and on the rates real parts
// report. Zero keeps its own answer because an unprogrammed register is
// the defect this gate exists for, not merely a value out of range.
static_assert(
    [] {
      return validate_timebase(0) == TimebaseError::kUnprogrammed &&
             validate_timebase(kMinCounterHz - 1) == TimebaseError::kOutOfRange &&
             validate_timebase(kMinCounterHz) == TimebaseError::kNone &&
             validate_timebase(24'000'000) == TimebaseError::kNone && // a common board oscillator
             validate_timebase(62'500'000) == TimebaseError::kNone && // QEMU virt
             validate_timebase(kMaxCounterHz) == TimebaseError::kNone &&
             validate_timebase(kMaxCounterHz + 1) == TimebaseError::kOutOfRange &&
             validate_timebase(~std::uint64_t{0}) == TimebaseError::kOutOfRange;
    }(),
    "the timebase window admits every real counter rate and nothing else");

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

// The second guard, at the exact remainder where it starts refusing.
// The whole seconds alone fit — the first guard admitted them — and it
// is the sub-second part that carries the sum past the end of the
// counter. On the fastest rate EL2 accepts, the room left above those
// seconds is kMax % hz ticks, so the largest remainder that still fits
// is that many ticks' worth and the next one over is refused. A plan
// that wrapped instead would arm a deadline already in the past.
static_assert(
    [] {
      constexpr std::uint64_t kMax    = std::numeric_limits<std::uint64_t>::max();
      constexpr std::uint64_t kHz     = kMaxCounterHz;
      constexpr std::uint64_t kFull   = kMax / kHz; // the most whole seconds the first guard lets through
      constexpr std::uint64_t kRoom   = kMax % kHz; // 709'551'615 ticks above them
      const TickPlan          ms_fits = ms_to_ticks(kHz, (kFull * 1000) + 709);
      const TickPlan          ms_over = ms_to_ticks(kHz, (kFull * 1000) + 710);
      const TickPlan          us_fits = us_to_ticks(kHz, (kFull * 1'000'000) + 709'551);
      const TickPlan          us_over = us_to_ticks(kHz, (kFull * 1'000'000) + 709'552);
      return kRoom == 709'551'615ULL &&                                          // where the two literals come from
             ms_fits.accepted && ms_fits.ticks == (kFull * kHz) + 709'000'000 && //
             !ms_over.accepted && ms_over.ticks == 0 &&                          // a refusal arms nothing
             us_fits.accepted && us_fits.ticks == (kFull * kHz) + 709'551'000 && //
             !us_over.accepted && us_over.ticks == 0;
    }(),
    "a remainder that would carry the tick count past the counter's end is refused, and the one below it is not");

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
