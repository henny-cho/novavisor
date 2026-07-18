// Host-side tests for nova/sync.hpp — mutual exclusion under real
// thread concurrency. The bare-metal target uses the same std::atomic
// code paths, so what these threads prove holds at EL2.

#include "nova/sync.hpp"

#include <cstdint>
#include <gtest/gtest.h>
#include <thread>
#include <vector>

namespace {

TEST(SpinLock, SerializesConcurrentIncrements) {
  nova::sync::SpinLock lock;
  std::uint64_t        counter = 0; // deliberately non-atomic — the lock must protect it

  constexpr int kThreads    = 4;
  constexpr int kIterations = 50'000;

  std::vector<std::thread> workers;
  workers.reserve(kThreads);
  for (int t = 0; t < kThreads; ++t) {
    workers.emplace_back([&] {
      for (int i = 0; i < kIterations; ++i) {
        nova::sync::Guard g{lock};
        ++counter;
      }
    });
  }
  for (auto& w : workers) {
    w.join();
  }

  EXPECT_EQ(counter, static_cast<std::uint64_t>(kThreads) * kIterations);
}

TEST(SpinLock, GuardReleasesOnScopeExit) {
  nova::sync::SpinLock lock;
  {
    nova::sync::Guard g{lock};
  }
  // Re-acquirable immediately — a leaked hold would deadlock here.
  lock.lock();
  lock.unlock();
}

} // namespace
