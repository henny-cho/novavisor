#include "smmu/queue_model.hpp"

#include <gtest/gtest.h>

namespace {

using nova::smmu::QueueState;

TEST(SmmuQueue, EmptyQueueTransitionsThroughOneEntry) {
  QueueState queue{.log2_entries = 2};
  EXPECT_TRUE(queue.empty());
  EXPECT_FALSE(queue.full());
  EXPECT_FALSE(queue.try_consume());

  EXPECT_TRUE(queue.try_produce());
  EXPECT_FALSE(queue.empty());
  EXPECT_EQ(queue.producer_index(), 1);

  EXPECT_TRUE(queue.try_consume());
  EXPECT_TRUE(queue.empty());
  EXPECT_EQ(queue.consumer_index(), 1);
}

TEST(SmmuQueue, FullUsesWrapPhaseAtMatchingIndex) {
  QueueState queue{.log2_entries = 2};
  for (std::uint32_t i = 0; i < queue.capacity(); ++i) {
    ASSERT_TRUE(queue.try_produce());
  }

  EXPECT_TRUE(queue.full());
  EXPECT_FALSE(queue.empty());
  EXPECT_EQ(queue.producer_index(), queue.consumer_index());
  EXPECT_NE(queue.producer & queue.wrap_mask(), queue.consumer & queue.wrap_mask());
  EXPECT_FALSE(queue.try_produce());
}

TEST(SmmuQueue, WrapsProducerAndConsumerWithoutAmbiguity) {
  QueueState queue{.log2_entries = 2};
  for (std::uint32_t round = 0; round < 2; ++round) {
    for (std::uint32_t i = 0; i < queue.capacity(); ++i) {
      ASSERT_TRUE(queue.try_produce());
    }
    EXPECT_TRUE(queue.full());
    for (std::uint32_t i = 0; i < queue.capacity(); ++i) {
      ASSERT_TRUE(queue.try_consume());
    }
    EXPECT_TRUE(queue.empty());
  }

  EXPECT_EQ(queue.producer & queue.pointer_mask(), 0);
  EXPECT_EQ(queue.consumer & queue.pointer_mask(), 0);
}

TEST(SmmuQueue, PreservesRegisterStatusBitsOnAdvance) {
  constexpr std::uint32_t status = 1U << 31U;
  QueueState              queue{.log2_entries = 2, .producer = status, .consumer = status};

  ASSERT_TRUE(queue.try_produce());
  EXPECT_EQ(queue.producer & status, status);
  ASSERT_TRUE(queue.try_consume());
  EXPECT_EQ(queue.consumer & status, status);
  EXPECT_TRUE(queue.empty());
}

TEST(SmmuQueue, RejectsImpossibleProducerDistance) {
  QueueState queue{.log2_entries = 2, .producer = 5, .consumer = 0};
  EXPECT_EQ(queue.used(), 5);
  EXPECT_FALSE(queue.consistent());
  EXPECT_FALSE(queue.empty());
  EXPECT_FALSE(queue.full());
  EXPECT_FALSE(queue.try_produce());
  EXPECT_FALSE(queue.try_consume());
}

TEST(SmmuQueue, EventOverflowTogglesOnceAndAcknowledgesProducer) {
  QueueState queue{.log2_entries = 1};
  ASSERT_TRUE(nova::smmu::try_record_event(queue));
  ASSERT_TRUE(nova::smmu::try_record_event(queue));
  ASSERT_TRUE(queue.full());

  EXPECT_FALSE(nova::smmu::try_record_event(queue));
  EXPECT_TRUE(nova::smmu::event_overflow_pending(queue));
  const std::uint32_t overflowed = queue.producer;

  EXPECT_FALSE(nova::smmu::try_record_event(queue));
  EXPECT_EQ(queue.producer, overflowed);

  nova::smmu::acknowledge_event_overflow(queue);
  EXPECT_FALSE(nova::smmu::event_overflow_pending(queue));
  EXPECT_EQ(queue.consumer & queue.pointer_mask(), 0);
}

TEST(SmmuQueue, RejectsUnrepresentableQueueSize) {
  QueueState queue{.log2_entries = 20};
  EXPECT_FALSE(queue.valid());
  EXPECT_EQ(queue.capacity(), 0);
  EXPECT_FALSE(queue.empty());
  EXPECT_FALSE(queue.full());
  EXPECT_FALSE(queue.try_produce());
  EXPECT_FALSE(queue.try_consume());
}

} // namespace
