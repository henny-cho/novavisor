// Host-side GTest suite for the tick conversions. The counter-rate
// window is pinned by a static_assert in the header; what is tested
// here is the arithmetic every bounded wait derives from — resolution
// on rates that do not divide evenly, and the overflow it must refuse.

#include "nova/arch/timebase.hpp"

#include <gtest/gtest.h>
#include <limits>

namespace {

using nova::arch::deadline_after_ms;
using nova::arch::ms_to_ticks;
using nova::arch::us_to_ticks;

constexpr std::uint64_t kMax     = std::numeric_limits<std::uint64_t>::max();
constexpr std::uint64_t kQemuHz  = 62'500'000; // QEMU virt
constexpr std::uint64_t kBoardHz = 100'000'000;

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

// Microsecond conversion backs the driver poll budgets, so a positive
// request must never round down to "already expired".
TEST(Timebase, ConvertsMicrosecondsWithoutRoundingToZero) {
  EXPECT_EQ(us_to_ticks(kQemuHz, 1).ticks, 62U);
  EXPECT_EQ(us_to_ticks(kBoardHz, 1).ticks, 100U);
  EXPECT_EQ(us_to_ticks(kBoardHz, 10'000).ticks, kBoardHz / 100);
  EXPECT_EQ(us_to_ticks(kBoardHz, 1'000'000).ticks, kBoardHz);
  // The validated window's floor still yields a tick per microsecond.
  EXPECT_EQ(us_to_ticks(nova::arch::kMinCounterHz, 1).ticks, 1U);
}

TEST(Timebase, MicrosecondConversionRejectsBadInput) {
  EXPECT_FALSE(us_to_ticks(0, 100).accepted);
  EXPECT_FALSE(us_to_ticks(kMax, 2'000'000).accepted);
  EXPECT_TRUE(us_to_ticks(kBoardHz, 0).accepted);
  EXPECT_EQ(us_to_ticks(kBoardHz, 0).ticks, 0U);
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
