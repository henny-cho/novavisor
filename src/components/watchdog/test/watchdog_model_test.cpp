#include "watchdog/watchdog_model.hpp"

#include <cstdint>
#include <gtest/gtest.h>
#include <limits>

namespace {

using nova::watchdog::accepts_generation;
using nova::watchdog::accepts_update;
using nova::watchdog::deadline_after_ms;

TEST(WatchdogModel, ZeroWindowDisarms) {
  const auto plan = deadline_after_ms(123, 0, 0);
  EXPECT_TRUE(plan.accepted);
  EXPECT_EQ(plan.deadline, 0U);
}

TEST(WatchdogModel, ConvertsMillisecondsWithoutLosingWholeSeconds) {
  const auto plan = deadline_after_ms(100, 24'000'000, 1'250);
  ASSERT_TRUE(plan.accepted);
  EXPECT_EQ(plan.deadline, 30'000'100U);
}

TEST(WatchdogModel, RejectsFrequencyAndDeadlineOverflow) {
  constexpr std::uint64_t max = std::numeric_limits<std::uint64_t>::max();
  EXPECT_FALSE(deadline_after_ms(0, max, 2'000).accepted);
  EXPECT_FALSE(deadline_after_ms(max - 10, 1'000, 20).accepted);
}

TEST(WatchdogModel, AcceptsOnlyCurrentLiveBootGeneration) {
  EXPECT_TRUE(accepts_generation(7, 7, true));
  EXPECT_FALSE(accepts_generation(6, 7, true));
  EXPECT_FALSE(accepts_generation(7, 7, false));
  EXPECT_FALSE(accepts_generation(0, 0, true));
}

TEST(WatchdogModel, LatestUpdateWinsWithinOneBootGeneration) {
  EXPECT_TRUE(accepts_update(7, 7, 12, 12, true));
  EXPECT_FALSE(accepts_update(7, 7, 11, 12, true));
  EXPECT_FALSE(accepts_update(6, 7, 12, 12, true));
}

} // namespace
