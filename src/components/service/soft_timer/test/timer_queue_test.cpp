// components/soft_timer/test/timer_queue_test.cpp
//
// Host-side tests for the pure deadline-slot queue.

#include "soft_timer/timer_queue.hpp"

#include <cstdint>
#include <gtest/gtest.h>
#include <vector>

namespace {

using nova::soft_timer::kNoDeadline;
using nova::soft_timer::TimerQueue;

using Queue = TimerQueue<4>;

std::vector<std::uint64_t> g_fired;

void record(nova::TrapContext* /*ctx*/, std::uint64_t arg) {
  g_fired.push_back(arg);
}

// Drain everything due at `now`, recording callback args in order.
auto drain(Queue& q, std::uint64_t now) -> std::vector<std::uint64_t> {
  g_fired.clear();
  Queue::Expired due;
  while (q.pop_expired(now, due)) {
    due.fn(nullptr, due.arg);
  }
  return g_fired;
}

TEST(TimerQueue, EmptyQueueHasNoDeadlineAndNothingDue) {
  Queue          q;
  Queue::Expired due;
  EXPECT_EQ(q.next_deadline(), kNoDeadline);
  EXPECT_FALSE(q.pop_expired(1000, due));
}

TEST(TimerQueue, NextDeadlineIsEarliestArmedSlot) {
  Queue q;
  q.arm(0, 300, &record, 0);
  q.arm(1, 100, &record, 1);
  q.arm(2, 200, &record, 2);
  EXPECT_EQ(q.next_deadline(), 100U);
}

TEST(TimerQueue, PopReturnsDueSlotsInDeadlineOrderAndDisarms) {
  Queue q;
  q.arm(0, 300, &record, 30);
  q.arm(1, 100, &record, 10);
  q.arm(2, 200, &record, 20);
  q.arm(3, 900, &record, 90); // not due

  EXPECT_EQ(drain(q, 500), (std::vector<std::uint64_t>{10, 20, 30}));
  EXPECT_EQ(q.next_deadline(), 900U); // due slots disarmed, [3] remains
}

TEST(TimerQueue, DeadlineEqualToNowIsDue) {
  Queue q;
  q.arm(0, 100, &record, 1);
  EXPECT_EQ(drain(q, 100), (std::vector<std::uint64_t>{1}));
}

TEST(TimerQueue, ReArmOverwritesSlot) {
  Queue q;
  q.arm(0, 100, &record, 1);
  q.arm(0, 500, &record, 2); // overwrite: old deadline/arg gone
  EXPECT_TRUE(drain(q, 200).empty());
  EXPECT_EQ(drain(q, 500), (std::vector<std::uint64_t>{2}));
}

TEST(TimerQueue, CancelDisarmsWithoutFiring) {
  Queue q;
  q.arm(0, 100, &record, 1);
  q.arm(1, 200, &record, 2);
  q.cancel(0);
  EXPECT_EQ(q.next_deadline(), 200U);
  EXPECT_EQ(drain(q, 300), (std::vector<std::uint64_t>{2}));
}

TEST(TimerQueue, CancelledSlotCanBeReArmed) {
  Queue q;
  q.arm(0, 100, &record, 1);
  q.cancel(0);
  q.arm(0, 150, &record, 3);
  EXPECT_EQ(drain(q, 150), (std::vector<std::uint64_t>{3}));
}

// A callback that re-arms its own slot mid-drain: the new deadline is
// honored on the next drain, not popped again in the current one when
// it lies in the future.
Queue* g_rearm_queue = nullptr;

void rearm_self(nova::TrapContext* /*ctx*/, std::uint64_t arg) {
  g_fired.push_back(arg);
  g_rearm_queue->arm(0, 1000, &record, arg + 1);
}

TEST(TimerQueue, CallbackMayReArmDuringDrain) {
  Queue q;
  g_rearm_queue = &q;
  q.arm(0, 100, &rearm_self, 7);

  EXPECT_EQ(drain(q, 200), (std::vector<std::uint64_t>{7}));
  EXPECT_EQ(q.next_deadline(), 1000U);
  EXPECT_EQ(drain(q, 1000), (std::vector<std::uint64_t>{8}));
}

} // namespace
