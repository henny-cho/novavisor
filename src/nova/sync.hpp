#pragma once

// nova/sync.hpp
//
// SMP synchronization primitives. Lives in the foundation tree because
// both hal (console serialization) and components (shared emulation
// state) need it. Every critical section in the hypervisor is a few
// loads/stores long, so the lock is a plain test-and-set spin — no
// queueing, no wait hints.

#include <atomic>

namespace nova::sync {

class SpinLock {
public:
  void lock() noexcept {
    while (flag_.test_and_set(std::memory_order_acquire)) {
      // spin — owners hold the lock for a handful of instructions
    }
  }

  void unlock() noexcept { flag_.clear(std::memory_order_release); }

private:
  std::atomic_flag flag_ = ATOMIC_FLAG_INIT;
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
