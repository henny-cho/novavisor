// Host-side tests for smp/quiesce_model.hpp — the quiesce-epoch
// tracker. The invariants under test:
//   - restore becomes legal only after every live vCPU ACKs the epoch,
//   - stale/duplicate ACKs and timeouts are ignored,
//   - retries are bounded and target only the still-pending vCPUs.

#include "smp/quiesce_model.hpp"

#include <gtest/gtest.h>

namespace {

using nova::lifecycle::AckResult;
using nova::lifecycle::kMaxQuiesceRetries;
using nova::lifecycle::TimeoutResult;
using Tracker = nova::lifecycle::QuiesceTracker<2>;

TEST(QuiesceModel, RestoreIsReadyOnlyAfterEveryAck) {
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

TEST(QuiesceModel, StaleAndDuplicateAcksAreIgnored) {
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

TEST(QuiesceModel, ConcurrentBeginIsRejected) {
  Tracker    tracker;
  const auto first  = tracker.begin(0b01U);
  const auto second = tracker.begin(0b10U);

  EXPECT_TRUE(first.accepted);
  EXPECT_FALSE(second.accepted);
  EXPECT_EQ(tracker.epoch(), first.epoch);
  EXPECT_EQ(tracker.pending_mask(), 0b01U);
}

TEST(QuiesceModel, EmptyLiveSetCanFinishImmediately) {
  Tracker    tracker;
  const auto plan = tracker.begin(0);

  EXPECT_TRUE(plan.accepted);
  EXPECT_TRUE(tracker.ready());
  EXPECT_TRUE(tracker.finish());
}

TEST(QuiesceModel, OutOfRangeAckIsIgnored) {
  Tracker    tracker;
  const auto plan = tracker.begin(0b01U);

  EXPECT_EQ(tracker.acknowledge(2, plan.epoch), AckResult::kIgnored);
  EXPECT_EQ(tracker.pending_mask(), 0b01U);
}

TEST(QuiesceModel, TimeoutRetriesAreBounded) {
  Tracker    tracker;
  const auto plan = tracker.begin(0b11U);

  for (std::uint8_t retry = 1; retry <= kMaxQuiesceRetries; ++retry) {
    EXPECT_EQ(tracker.on_timeout(plan.epoch), TimeoutResult::kRetry);
    EXPECT_EQ(tracker.retries(), retry);
    EXPECT_EQ(tracker.pending_mask(), 0b11U);
  }
  EXPECT_EQ(tracker.on_timeout(plan.epoch), TimeoutResult::kFailed);
  EXPECT_TRUE(tracker.active());
  EXPECT_EQ(tracker.pending_mask(), 0b11U);
}

TEST(QuiesceModel, TimeoutRetriesOnlyPendingVcpus) {
  Tracker    tracker;
  const auto plan = tracker.begin(0b11U);

  ASSERT_EQ(tracker.acknowledge(0, plan.epoch), AckResult::kPending);
  EXPECT_EQ(tracker.on_timeout(plan.epoch), TimeoutResult::kRetry);
  EXPECT_EQ(tracker.pending_mask(), 0b10U);
  EXPECT_EQ(tracker.acknowledge(1, plan.epoch), AckResult::kReady);
  EXPECT_EQ(tracker.on_timeout(plan.epoch), TimeoutResult::kIgnored);
}

TEST(QuiesceModel, StaleTimeoutIsIgnored) {
  Tracker    tracker;
  const auto first = tracker.begin(0b01U);
  ASSERT_EQ(tracker.acknowledge(0, first.epoch), AckResult::kReady);
  ASSERT_TRUE(tracker.finish());

  const auto second = tracker.begin(0b01U);
  EXPECT_EQ(tracker.on_timeout(first.epoch), TimeoutResult::kIgnored);
  EXPECT_EQ(tracker.retries(), 0);
  EXPECT_EQ(tracker.pending_mask(), second.pending_mask);
}

} // namespace
