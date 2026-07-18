#pragma once

// components/soft_timer/include/soft_timer/timer_queue.hpp
//
// Fixed-slot deadline queue — the pure, host-testable core of the
// soft_timer component. Each client owns a compile-time slot index;
// arming an armed slot overwrites it. Deadlines are absolute counter
// values; the driver programs the hardware one-shot to next_deadline()
// and loops pop_expired() until the queue drains.

#include <array>
#include <cstddef>
#include <cstdint>

namespace nova {
struct TrapContext; // callbacks carry the live trap frame by pointer only
} // namespace nova

namespace nova::soft_timer {

// Expiry callback. `ctx` is the live trap frame of the interrupted
// guest — callbacks may swap it (preemption); `arg` is the client's
// arm-time value.
using Callback = void (*)(TrapContext* ctx, std::uint64_t arg);

inline constexpr std::uint64_t kNoDeadline = ~0ULL;

template <std::size_t N>
class TimerQueue {
public:
  struct Expired {
    Callback      fn  = nullptr;
    std::uint64_t arg = 0;
  };

  void arm(std::size_t slot, std::uint64_t deadline, Callback fn, std::uint64_t arg) noexcept {
    slots_[slot] = Slot{.deadline = deadline, .fn = fn, .arg = arg, .armed = true};
  }

  void cancel(std::size_t slot) noexcept { slots_[slot].armed = false; }

  // Earliest armed deadline; kNoDeadline when nothing is armed.
  [[nodiscard]] auto next_deadline() const noexcept -> std::uint64_t {
    std::uint64_t next = kNoDeadline;
    for (const Slot& s : slots_) {
      if (s.armed && s.deadline < next) {
        next = s.deadline;
      }
    }
    return next;
  }

  // Disarm and return the earliest slot due at `now` (deadline order
  // keeps multi-expiry runs deterministic); false when none is due.
  // Callers loop — a callback may re-arm slots mid-drain.
  [[nodiscard]] auto pop_expired(std::uint64_t now, Expired& out) noexcept -> bool {
    std::size_t   due_idx      = N;
    std::uint64_t due_deadline = kNoDeadline;
    for (std::size_t i = 0; i < N; ++i) {
      if (slots_[i].armed && slots_[i].deadline <= now && slots_[i].deadline < due_deadline) {
        due_idx      = i;
        due_deadline = slots_[i].deadline;
      }
    }
    if (due_idx == N) {
      return false;
    }
    slots_[due_idx].armed = false;
    out                   = Expired{.fn = slots_[due_idx].fn, .arg = slots_[due_idx].arg};
    return true;
  }

private:
  struct Slot {
    std::uint64_t deadline = 0;
    Callback      fn       = nullptr;
    std::uint64_t arg      = 0;
    bool          armed    = false;
  };

  std::array<Slot, N> slots_{};
};

} // namespace nova::soft_timer
