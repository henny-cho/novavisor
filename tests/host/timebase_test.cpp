#include "nova/arch/timebase.hpp"

#include <gtest/gtest.h>
#include <limits>

namespace {

using nova::arch::deadline_after_ms;
using nova::arch::ms_to_ticks;
using nova::arch::TimebaseError;
using nova::arch::validate_timebase;

constexpr std::uint64_t kMax     = std::numeric_limits<std::uint64_t>::max();
constexpr std::uint64_t kQemuHz  = 62'500'000; // QEMU virt
constexpr std::uint64_t kBoardHz = 100'000'000;

TEST(Timebase, AcceptsRealCounterRates) {
  EXPECT_EQ(validate_timebase(kQemuHz), TimebaseError::kNone);
  EXPECT_EQ(validate_timebase(kBoardHz), TimebaseError::kNone);
  EXPECT_EQ(validate_timebase(24'000'000), TimebaseError::kNone);
}

// The defect this gate exists for: firmware that never programs
// CNTFRQ_EL0 leaves it reading zero, and every deadline collapses.
TEST(Timebase, RejectsUnprogrammedRegister) {
  EXPECT_EQ(validate_timebase(0), TimebaseError::kUnprogrammed);
}

TEST(Timebase, RejectsRatesOutsideTheUsableWindow) {
  EXPECT_EQ(validate_timebase(999'999), TimebaseError::kOutOfRange);
  EXPECT_EQ(validate_timebase(kMax), TimebaseError::kOutOfRange);
  EXPECT_EQ(validate_timebase(nova::arch::kMinCounterHz), TimebaseError::kNone);
  EXPECT_EQ(validate_timebase(nova::arch::kMaxCounterHz), TimebaseError::kNone);
}

TEST(Timebase, ConvertsWholeAndFractionalMilliseconds) {
  EXPECT_EQ(ms_to_ticks(kBoardHz, 1000).ticks, kBoardHz);
  EXPECT_EQ(ms_to_ticks(kBoardHz, 1).ticks, kBoardHz / 1000);
  EXPECT_EQ(ms_to_ticks(kBoardHz, 1250).ticks, kBoardHz + kBoardHz / 4);
  EXPECT_EQ(ms_to_ticks(kQemuHz, 10).ticks, 625'000); // the 10 ms time slice
}

// A rate that is not a whole multiple of 1000 must not lose the
// remainder — the fraction term carries it.
TEST(Timebase, KeepsSubMillisecondResolutionOnOddRates) {
  constexpr std::uint64_t kOddHz = 1'000'001;
  EXPECT_EQ(ms_to_ticks(kOddHz, 1000).ticks, kOddHz);
  EXPECT_EQ(ms_to_ticks(kOddHz, 1).ticks, 1000);
}

TEST(Timebase, RejectsConversionWithoutAFrequency) {
  EXPECT_FALSE(ms_to_ticks(0, 10).accepted);
  EXPECT_EQ(ms_to_ticks(0, 10).ticks, 0);
}

TEST(Timebase, RejectsConversionThatWouldOverflow) {
  EXPECT_FALSE(ms_to_ticks(kMax, 2000).accepted);
  EXPECT_FALSE(ms_to_ticks(kMax / 2, 4000).accepted);
}

TEST(Timebase, ZeroMillisecondsIsAZeroTickPlan) {
  const auto plan = ms_to_ticks(kBoardHz, 0);
  EXPECT_TRUE(plan.accepted);
  EXPECT_EQ(plan.ticks, 0);
}

TEST(Timebase, DeadlineAddsTicksToNow) {
  const auto plan = deadline_after_ms(100, 24'000'000, 1'250);
  EXPECT_TRUE(plan.accepted);
  EXPECT_EQ(plan.deadline, 100 + 24'000'000 + 6'000'000);
}

TEST(Timebase, DeadlineRejectsWrapAroundAndBadFrequency) {
  EXPECT_FALSE(deadline_after_ms(kMax - 10, 1'000'000, 20).accepted);
  EXPECT_FALSE(deadline_after_ms(0, kMax, 2'000).accepted);
  EXPECT_FALSE(deadline_after_ms(123, 0, 10).accepted);
}

} // namespace
