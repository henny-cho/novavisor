#pragma once

// components/core_vcpu/include/core_vcpu/lifecycle_model.hpp
//
// Pure VM micro-reboot policy, host-testable. A crash-looping guest
// (hangs again right after every reboot) would otherwise keep the
// watchdog resetting it forever — each VM gets a fixed warm-reset
// budget per cold start; once spent, the VM is stopped and only an
// explicit start_vm revives it (with a fresh budget).

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
  [[nodiscard]] auto take(std::size_t index) noexcept -> bool {
    if (counts_[index] >= kMaxRestarts) {
      return false;
    }
    ++counts_[index];
    return true;
  }

  // Cold start: a fresh budget.
  void refill(std::size_t index) noexcept { counts_[index] = 0; }

private:
  std::array<std::uint8_t, N> counts_{};
};

} // namespace nova::lifecycle
