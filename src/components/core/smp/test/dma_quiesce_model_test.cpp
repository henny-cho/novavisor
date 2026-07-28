#include "smp/dma_quiesce_model.hpp"

#include <gtest/gtest.h>

namespace {

using nova::dma_quiesce_outcome;
using nova::DmaQuiesceResult;

// A composition without a DMA-capable device leaves the request
// unclaimed. Treating that as failure would deny VM power to every
// device-free profile; treating it as pending would hang the reset.
TEST(DmaQuiesce, UnclaimedRequestIsAlreadySatisfied) {
  EXPECT_EQ(dma_quiesce_outcome(false, DmaQuiesceResult::kPending), DmaQuiesceResult::kComplete);
  EXPECT_EQ(dma_quiesce_outcome(false, DmaQuiesceResult::kFailed), DmaQuiesceResult::kComplete);
  EXPECT_EQ(dma_quiesce_outcome(false, DmaQuiesceResult::kComplete), DmaQuiesceResult::kComplete);
}

// A subscribed device stack owns the answer, including the negative one.
TEST(DmaQuiesce, ClaimedRequestKeepsTheSubscriberVerdict) {
  EXPECT_EQ(dma_quiesce_outcome(true, DmaQuiesceResult::kComplete), DmaQuiesceResult::kComplete);
  EXPECT_EQ(dma_quiesce_outcome(true, DmaQuiesceResult::kPending), DmaQuiesceResult::kPending);
  EXPECT_EQ(dma_quiesce_outcome(true, DmaQuiesceResult::kFailed), DmaQuiesceResult::kFailed);
}

} // namespace
