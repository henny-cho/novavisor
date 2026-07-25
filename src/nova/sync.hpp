#pragma once

// nova/sync.hpp
//
// SMP synchronization primitives. Lives in the foundation tree because
// both hal (console serialization) and components (shared emulation
// state) need it. Critical sections are short, but a plain
// test-and-set spin is unfair: under sustained contention (one guest
// flooding the console while another prints a line) the same core can
// win indefinitely and starve the other — observed under TCG. The
// ticket handshake grants the lock in FIFO order instead.

#include <atomic>
#include <cstdint>

namespace nova::sync {

class SpinLock {
public:
  void lock() noexcept {
    const std::uint32_t ticket = next_.fetch_add(1, std::memory_order_relaxed);
    while (serving_.load(std::memory_order_acquire) != ticket) {
#if defined(__aarch64__)
      __asm__ volatile("yield"); // spin — FIFO turn is coming
#endif
    }
  }

  void unlock() noexcept {
    // Sole owner: no competing writer to serving_ until this store.
    serving_.store(serving_.load(std::memory_order_relaxed) + 1, std::memory_order_release);
  }

private:
  // Separate cache lines: every waiter polls serving_, so a ticket
  // grab (fetch_add on next_) must not invalidate the polled line.
  alignas(64) std::atomic<std::uint32_t> next_{0};
  alignas(64) std::atomic<std::uint32_t> serving_{0};
};

// Scoped ownership for the common lock-around-a-block shape.
class Guard {
public:
  explicit Guard(SpinLock& lock) noexcept : lock_(lock) { lock_.lock(); }
  ~Guard() { lock_.unlock(); }
  Guard(const Guard&)                    = delete;
  auto operator=(const Guard&) -> Guard& = delete;
  Guard(Guard&&)                         = delete;
  auto operator=(Guard&&) -> Guard&      = delete;

private:
  SpinLock& lock_;
};

// Advance a monotonic counter and return its new value, skipping zero on
// wrap. Zero is reserved by the counters' readers to mean "never" (no
// generation, no update yet), so it must never be handed out as a live
// value. Contended increments retry; every caller gets a distinct value.
[[nodiscard]] inline auto next_nonzero(std::atomic<std::uint64_t>& counter) noexcept -> std::uint64_t {
  std::uint64_t current = counter.load(std::memory_order_relaxed);
  for (;;) {
    std::uint64_t next = current + 1U;
    if (next == 0) {
      next = 1;
    }
    if (counter.compare_exchange_weak(current, next, std::memory_order_acq_rel, std::memory_order_relaxed)) {
      return next;
    }
  }
}

} // namespace nova::sync
