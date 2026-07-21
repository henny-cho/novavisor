#pragma once

// components/core_vcpu/include/core_vcpu/lifecycle_model.hpp
//
// Pure VM micro-reboot policy, host-testable. RestartBudget limits
// crash loops; QuiesceTracker prevents memory restore until every live
// vCPU has acknowledged the current reset epoch.

#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>

namespace nova::lifecycle {

// Warm resets allowed between cold starts. Deliberately small: a guest
// that cannot recover in a few attempts will not recover in fifty.
inline constexpr std::uint8_t kMaxRestarts = 3;

enum class AckResult : std::uint8_t { kIgnored, kPending, kReady };

struct QuiescePlan {
  bool          accepted     = false;
  std::uint64_t epoch        = 0;
  std::uint32_t pending_mask = 0;
};

template <std::size_t MaxVcpus>
class QuiesceTracker {
  static constexpr std::size_t kMaskBits = std::numeric_limits<std::uint32_t>::digits;
  static_assert(MaxVcpus > 0 && MaxVcpus <= kMaskBits);

  [[nodiscard]] static consteval auto allowed_mask() noexcept -> std::uint32_t {
    if constexpr (MaxVcpus == kMaskBits) {
      return ~std::uint32_t{0};
    } else {
      return (std::uint32_t{1} << MaxVcpus) - 1U;
    }
  }

public:
  [[nodiscard]] auto begin(std::uint32_t live_mask) noexcept -> QuiescePlan {
    if (active_) {
      return {};
    }
    ++epoch_;
    if (epoch_ == 0) {
      ++epoch_; // reserve zero for an uninitialized request
    }
    pending_mask_ = live_mask & allowed_mask();
    active_       = true;
    return {.accepted = true, .epoch = epoch_, .pending_mask = pending_mask_};
  }

  [[nodiscard]] auto acknowledge(std::size_t vcpu, std::uint64_t epoch) noexcept -> AckResult {
    if (!active_ || epoch != epoch_ || vcpu >= MaxVcpus) {
      return AckResult::kIgnored;
    }
    const std::uint32_t bit = std::uint32_t{1} << vcpu;
    if ((pending_mask_ & bit) == 0U) {
      return AckResult::kIgnored;
    }
    pending_mask_ &= ~bit;
    return pending_mask_ == 0U ? AckResult::kReady : AckResult::kPending;
  }

  [[nodiscard]] auto ready() const noexcept -> bool { return active_ && pending_mask_ == 0U; }
  [[nodiscard]] auto active() const noexcept -> bool { return active_; }
  [[nodiscard]] auto epoch() const noexcept -> std::uint64_t { return epoch_; }
  [[nodiscard]] auto pending_mask() const noexcept -> std::uint32_t { return pending_mask_; }

  [[nodiscard]] auto finish() noexcept -> bool {
    if (!ready()) {
      return false;
    }
    active_ = false;
    return true;
  }

  void cancel() noexcept {
    active_       = false;
    pending_mask_ = 0;
  }

private:
  std::uint64_t epoch_        = 0;
  std::uint32_t pending_mask_ = 0;
  bool          active_       = false;
};

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
