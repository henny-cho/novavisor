#pragma once

// components/core_vcpu/include/core_vcpu/lifecycle_model.hpp
//
// Pure VM micro-reboot policy, host-testable. RestartBudget limits
// crash loops. (Quiesce-epoch tracking lives with its only driver:
// smp/quiesce_model.hpp.)

#include <array>
#include <cstddef>
#include <cstdint>

namespace nova::lifecycle {

// Warm resets allowed between cold starts. Deliberately small: a guest
// that cannot recover in a few attempts will not recover in fifty.
inline constexpr std::uint8_t kMaxRestarts = 3;

template <std::size_t N>
class RestartBudget {
public:
  // Spend one restart. False when the budget is exhausted — the caller
  // stops the VM instead of resetting it.
  [[nodiscard]] constexpr auto take(std::size_t index) noexcept -> bool {
    if (counts_[index] >= kMaxRestarts) {
      return false;
    }
    ++counts_[index];
    return true;
  }

  // Cold start: a fresh budget.
  constexpr void refill(std::size_t index) noexcept { counts_[index] = 0; }

private:
  std::array<std::uint8_t, N> counts_{};
};

// Two VMs spending independently: the count each one gets, that denial
// persists once it is spent, and that a cold start returns the budget of
// the VM it names and no other. A shared counter would let one crash
// loop deny every other VM its recovery.
static_assert(
    [] {
      RestartBudget<2> budget;
      unsigned         first  = 0;
      unsigned         second = 0;
      while (budget.take(0)) {
        ++first;
      }
      while (budget.take(1)) {
        ++second;
      }
      budget.refill(0);
      return first == kMaxRestarts && second == kMaxRestarts && !budget.take(1) && // still exhausted
             budget.take(0);                                                       // refilled, and only this one
    }(),
    "each VM spends exactly its own restart budget, and only its own cold start returns it");

} // namespace nova::lifecycle
