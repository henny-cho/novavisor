#pragma once

// hal/timer.hpp
//
// Generic-timer facade; the arch tree is selected at build time. The
// hypervisor owns the EL2 physical timer, guests keep the virtual one.

#include "hal/arch/aarch64/timer.hpp"
#include "nova/arch/timebase.hpp"

#include <cstdint>

namespace nova {

namespace hyp_timer = arch::hyp_timer;

namespace timer {

// A bounded wait measured in time, not loop iterations. An iteration
// count bears no relation to a silicon clock or a device's latency: the
// same constant is generous on one part and a hang on another, and it
// silently changes meaning with every compiler and -O level.
//
// Falls back to counting when no timebase has been adopted, and that
// case is not hypothetical: the boot contract gate reports an unusable
// CNTFRQ_EL0 through the console, whose transmit wait is one of these.
// A budget that required the timebase could not bound the report of the
// timebase being unusable.
class Budget {
public:
  explicit Budget(std::uint64_t microseconds) noexcept {
    if (const auto plan = arch::us_to_ticks(hyp_timer::freq(), microseconds); plan.accepted) {
      deadline_ = hyp_timer::now() + plan.ticks;
    }
  }

  [[nodiscard]] auto expired() noexcept -> bool {
    if (deadline_ != 0U) {
      return hyp_timer::now() >= deadline_;
    }
    return iterations_-- == 0U;
  }

private:
  // Pre-timebase fallback only. Sized to outlast any real handshake at
  // one iteration per few instructions, while still ending.
  static constexpr std::uint32_t kFallbackIterations = 1'000'000;

  std::uint64_t deadline_   = 0;
  std::uint32_t iterations_ = kFallbackIterations;
};

} // namespace timer
} // namespace nova
