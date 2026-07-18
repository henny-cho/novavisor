// components/core_vcpu/test/sched_model_test.cpp
//
// Host-side tests for the pure scheduler core.

#include "core_vcpu/sched_model.hpp"

#include <array>
#include <gtest/gtest.h>

namespace {

using nova::sched::all_off;
using nova::sched::pick_next;
using nova::sched::slice_needed;
using nova::sched::State;

constexpr auto kOff     = State::kOff;
constexpr auto kReady   = State::kReady;
constexpr auto kRunning = State::kRunning;
constexpr auto kBlocked = State::kBlocked;

TEST(SchedModel, PickScansRingOrderFromCurrent) {
  const std::array s{kReady, kRunning, kReady, kReady};
  EXPECT_EQ(pick_next(s, 1), 2U); // first ready after current
  EXPECT_EQ(pick_next(s, 3), 0U); // wraps around
}

TEST(SchedModel, PickSkipsOffAndBlocked) {
  const std::array s{kRunning, kOff, kBlocked, kReady};
  EXPECT_EQ(pick_next(s, 0), 3U);
}

TEST(SchedModel, PickConsidersCurrentLast) {
  // The current VCPU may have been woken while scheduling out (idle):
  // it is picked only when nothing else is ready.
  const std::array s{kReady, kOff, kOff, kOff};
  EXPECT_EQ(pick_next(s, 0), 0U);

  const std::array two{kReady, kReady, kOff, kOff};
  EXPECT_EQ(pick_next(two, 0), 1U); // the other one wins
}

TEST(SchedModel, PickReturnsSizeWhenNothingRunnable) {
  const std::array s{kBlocked, kOff, kBlocked, kOff};
  EXPECT_EQ(pick_next(s, 0), s.size());
}

TEST(SchedModel, AllOffOnlyWhenEveryVcpuRetired) {
  const std::array done{kOff, kOff};
  const std::array asleep{kOff, kBlocked};
  EXPECT_TRUE(all_off(done));
  EXPECT_FALSE(all_off(asleep)); // blocked VCPU still owed a wake-up
}

TEST(SchedModel, SliceNeededOnlyWithReadyCompetitor) {
  const std::array contended{kRunning, kReady};
  const std::array alone{kRunning, kBlocked, kOff, kOff};
  EXPECT_TRUE(slice_needed(contended));
  EXPECT_FALSE(slice_needed(alone));
}

} // namespace
