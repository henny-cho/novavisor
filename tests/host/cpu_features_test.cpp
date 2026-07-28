#include "nova/arch/cpu_features.hpp"

#include <gtest/gtest.h>

namespace {

using nova::arch::branch_history_mitigation;
using nova::arch::branch_target_mitigation;
using nova::arch::fault_channel_mitigation;
using nova::arch::Mitigation;
using nova::arch::read_speculation_state;
using nova::arch::store_bypass_mitigation;

// CSV2 at [59:56], CSV3 at [63:60].
constexpr auto pfr0(std::uint64_t csv2, std::uint64_t csv3) -> std::uint64_t {
  return (csv2 << 56U) | (csv3 << 60U);
}
// SSBS at [7:4], CSV2_frac at [35:32].
constexpr auto pfr1(std::uint64_t ssbs, std::uint64_t csv2_frac) -> std::uint64_t {
  return (ssbs << 4U) | (csv2_frac << 32U);
}
// CLRBHB at [31:28].
constexpr auto isar2(std::uint64_t clrbhb) -> std::uint64_t {
  return clrbhb << 28U;
}

TEST(CpuFeatures, DecodesEachField) {
  const auto s = read_speculation_state(pfr0(2, 1), pfr1(2, 1), isar2(1));
  EXPECT_EQ(s.csv2, 2U);
  EXPECT_EQ(s.csv3, 1U);
  EXPECT_EQ(s.ssbs, 2U);
  EXPECT_EQ(s.csv2_frac, 1U);
  EXPECT_TRUE(s.clrbhb);
}

TEST(CpuFeatures, IgnoresNeighbouringFields) {
  // Every bit set: each accessor must still yield only its own nibble.
  const auto all = read_speculation_state(~std::uint64_t{0}, ~std::uint64_t{0}, ~std::uint64_t{0});
  EXPECT_EQ(all.csv2, 0xFU);
  EXPECT_EQ(all.csv3, 0xFU);
  EXPECT_EQ(all.ssbs, 0xFU);
  EXPECT_EQ(all.csv2_frac, 0xFU);
  EXPECT_TRUE(all.clrbhb);

  // Every bit except the CSV2 nibble: neighbours must not leak into it.
  constexpr std::uint64_t kCsv2Mask = 0xFULL << 56U;
  const auto              without   = read_speculation_state(~kCsv2Mask, 0, 0);
  EXPECT_EQ(without.csv2, 0U);
  EXPECT_EQ(without.csv3, 0xFU);
}

// A zero field discloses nothing, so the hypervisor must not claim the
// PE is unaffected — that claim is exactly what SMCCC NOT_REQUIRED makes.
TEST(CpuFeatures, UndisclosedFieldsYieldUnknown) {
  const auto s = read_speculation_state(0, 0, 0);
  EXPECT_EQ(branch_target_mitigation(s), Mitigation::kUnknown);
  EXPECT_EQ(store_bypass_mitigation(s), Mitigation::kUnknown);
  EXPECT_EQ(branch_history_mitigation(s), Mitigation::kUnknown);
  EXPECT_EQ(fault_channel_mitigation(s), Mitigation::kUnknown);
}

TEST(CpuFeatures, BranchTargetFollowsCsv2) {
  EXPECT_EQ(branch_target_mitigation(read_speculation_state(pfr0(1, 0), 0, 0)), Mitigation::kUnaffected);
  EXPECT_EQ(branch_target_mitigation(read_speculation_state(pfr0(3, 0), 0, 0)), Mitigation::kUnaffected);
}

TEST(CpuFeatures, StoreBypassFollowsSsbs) {
  EXPECT_EQ(store_bypass_mitigation(read_speculation_state(0, pfr1(1, 0), 0)), Mitigation::kUnaffected);
  EXPECT_EQ(store_bypass_mitigation(read_speculation_state(0, pfr1(2, 0), 0)), Mitigation::kUnaffected);
}

TEST(CpuFeatures, BranchHistoryNeedsCsv2LevelTwoOrHigher) {
  EXPECT_EQ(branch_history_mitigation(read_speculation_state(pfr0(3, 0), 0, 0)), Mitigation::kUnaffected);
  EXPECT_EQ(branch_history_mitigation(read_speculation_state(pfr0(1, 0), pfr1(0, 2), 0)), Mitigation::kUnaffected);
  // CSV2 == 1 without the fraction refinement covers branch targets only.
  EXPECT_EQ(branch_history_mitigation(read_speculation_state(pfr0(1, 0), pfr1(0, 1), 0)), Mitigation::kUnknown);
}

// CLRBHB is a guest-side instruction: the guest can act, but the PE is
// still affected — the honest SMCCC answer is NOT_SUPPORTED.
TEST(CpuFeatures, ClrbhbIsAGuestSideMitigationNotAnUnaffectedPe) {
  EXPECT_EQ(branch_history_mitigation(read_speculation_state(pfr0(1, 0), pfr1(0, 1), isar2(1))),
            Mitigation::kGuestMitigates);
}

TEST(CpuFeatures, FaultChannelFollowsCsv3) {
  EXPECT_EQ(fault_channel_mitigation(read_speculation_state(pfr0(0, 1), 0, 0)), Mitigation::kUnaffected);
}

} // namespace
