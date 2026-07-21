// Host-side tests for core_vcpu/lifecycle_model.hpp — the micro-reboot
// budget. The invariants under test:
//   - a VM gets exactly kMaxRestarts warm resets per cold start,
//   - exhaustion denies further resets until a refill,
//   - budgets are independent per VM.

#include "core_vcpu/lifecycle_model.hpp"

#include <gtest/gtest.h>

namespace {

using nova::lifecycle::AckResult;
using nova::lifecycle::kMaxRestarts;
using Budget  = nova::lifecycle::RestartBudget<2>;
using Tracker = nova::lifecycle::QuiesceTracker<2>;

TEST(LifecycleModel, RestoreIsReadyOnlyAfterEveryAck) {
  Tracker    tracker;
  const auto plan = tracker.begin(0b11U);

  ASSERT_TRUE(plan.accepted);
  EXPECT_FALSE(tracker.ready());
  EXPECT_EQ(tracker.acknowledge(1, plan.epoch), AckResult::kPending);
  EXPECT_FALSE(tracker.ready());
  EXPECT_EQ(tracker.acknowledge(0, plan.epoch), AckResult::kReady);
  EXPECT_TRUE(tracker.ready());
  EXPECT_TRUE(tracker.finish());
  EXPECT_FALSE(tracker.active());
}

TEST(LifecycleModel, StaleAndDuplicateAcksAreIgnored) {
  Tracker    tracker;
  const auto first = tracker.begin(0b01U);
  ASSERT_EQ(tracker.acknowledge(0, first.epoch), AckResult::kReady);
  ASSERT_TRUE(tracker.finish());

  const auto second = tracker.begin(0b11U);
  ASSERT_NE(first.epoch, second.epoch);
  EXPECT_EQ(tracker.acknowledge(0, first.epoch), AckResult::kIgnored);
  EXPECT_EQ(tracker.pending_mask(), 0b11U);
  EXPECT_EQ(tracker.acknowledge(0, second.epoch), AckResult::kPending);
  EXPECT_EQ(tracker.acknowledge(0, second.epoch), AckResult::kIgnored);
  EXPECT_EQ(tracker.pending_mask(), 0b10U);
}

TEST(LifecycleModel, ConcurrentBeginIsRejected) {
  Tracker    tracker;
  const auto first  = tracker.begin(0b01U);
  const auto second = tracker.begin(0b10U);

  EXPECT_TRUE(first.accepted);
  EXPECT_FALSE(second.accepted);
  EXPECT_EQ(tracker.epoch(), first.epoch);
  EXPECT_EQ(tracker.pending_mask(), 0b01U);
}

TEST(LifecycleModel, EmptyLiveSetCanFinishImmediately) {
  Tracker    tracker;
  const auto plan = tracker.begin(0);

  EXPECT_TRUE(plan.accepted);
  EXPECT_TRUE(tracker.ready());
  EXPECT_TRUE(tracker.finish());
}

TEST(LifecycleModel, OutOfRangeAckIsIgnored) {
  Tracker    tracker;
  const auto plan = tracker.begin(0b01U);

  EXPECT_EQ(tracker.acknowledge(2, plan.epoch), AckResult::kIgnored);
  EXPECT_EQ(tracker.pending_mask(), 0b01U);
}

TEST(LifecycleModel, GrantsExactlyMaxRestarts) {
  Budget b;
  for (unsigned i = 0; i < kMaxRestarts; ++i) {
    EXPECT_TRUE(b.take(0)) << "restart " << i;
  }
  EXPECT_FALSE(b.take(0));
}

TEST(LifecycleModel, ExhaustionStaysDeniedUntilRefill) {
  Budget b;
  while (b.take(0)) {
  }
  EXPECT_FALSE(b.take(0));
  b.refill(0);
  EXPECT_TRUE(b.take(0));
}

TEST(LifecycleModel, BudgetsAreIndependentPerVm) {
  Budget b;
  while (b.take(0)) {
  }
  EXPECT_FALSE(b.take(0));
  EXPECT_TRUE(b.take(1)); // VM 1 untouched by VM 0's crash loop
}

TEST(LifecycleModel, RefillIsPerVm) {
  Budget b;
  while (b.take(0)) {
  }
  while (b.take(1)) {
  }
  b.refill(0);
  EXPECT_TRUE(b.take(0));
  EXPECT_FALSE(b.take(1));
}

} // namespace
