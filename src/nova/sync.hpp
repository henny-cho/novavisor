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
      // spin — FIFO turn is coming
    }
  }

  void unlock() noexcept {
    // Sole owner: no competing writer to serving_ until this store.
    serving_.store(serving_.load(std::memory_order_relaxed) + 1, std::memory_order_release);
  }

private:
  std::atomic<std::uint32_t> next_{0};
  std::atomic<std::uint32_t> serving_{0};
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

} // namespace nova::sync
