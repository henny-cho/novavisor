// Host-side tests for psci/psci_model.hpp — SMCCC range claiming and
// per-function verdicts. The invariants under test:
//   - only the PSCI range is claimed (NOVA IDs pass through untouched),
//   - SMC64 twins behave like their SMC32 forms,
//   - unimplemented-but-in-range functions answer NOT_SUPPORTED,
//   - FEATURES reports exactly the implemented set.

#include "psci/psci_model.hpp"

#include <cstdint>
#include <gtest/gtest.h>

namespace {

using nova::psci::Action;
using nova::psci::dispatch;

constexpr std::uint64_t kNotSupported = static_cast<std::uint64_t>(PSCI_NOT_SUPPORTED);

TEST(PsciModel, ClaimsOnlyThePsciRange) {
  EXPECT_FALSE(dispatch(0x1000, 0).claimed);     // NOVA demo ABI
  EXPECT_FALSE(dispatch(0x84000020, 0).claimed); // one past the range
  EXPECT_FALSE(dispatch(0x80000000, 0).claimed); // SMCCC arch range
  EXPECT_TRUE(dispatch(PSCI_FN_VERSION, 0).claimed);
  EXPECT_TRUE(dispatch(PSCI_FN_CPU_ON | PSCI_FN_SMC64, 0).claimed);
}

TEST(PsciModel, VersionReports11) {
  const auto v = dispatch(PSCI_FN_VERSION, 0);
  EXPECT_EQ(v.action, Action::kNone);
  EXPECT_EQ(v.ret, static_cast<std::uint64_t>(PSCI_VERSION_1_1));
}

TEST(PsciModel, SystemOffAndResetMapToActions) {
  EXPECT_EQ(dispatch(PSCI_FN_SYSTEM_OFF, 0).action, Action::kSystemOff);
  EXPECT_EQ(dispatch(PSCI_FN_SYSTEM_RESET, 0).action, Action::kSystemReset);
}

TEST(PsciModel, CpuOnOffAndAffinityInfoMapToActions) {
  EXPECT_EQ(dispatch(PSCI_FN_CPU_ON, 1).action, Action::kCpuOn);
  EXPECT_EQ(dispatch(PSCI_FN_CPU_OFF, 0).action, Action::kCpuOff);
  EXPECT_EQ(dispatch(PSCI_FN_AFFINITY_INFO, 1).action, Action::kAffinityInfo);
}

TEST(PsciModel, UnimplementedInRangeAnswersNotSupported) {
  const auto v = dispatch(0x84000001, 0); // CPU_SUSPEND
  EXPECT_TRUE(v.claimed);
  EXPECT_EQ(v.action, Action::kNone);
  EXPECT_EQ(v.ret, kNotSupported);
}

TEST(PsciModel, TargetVcpuAcceptsOnlyAff0) {
  EXPECT_EQ(nova::psci::target_vcpu(0), 0U);
  EXPECT_EQ(nova::psci::target_vcpu(1), 1U);
  EXPECT_EQ(nova::psci::target_vcpu(0x100), nova::psci::kInvalidTarget);       // Aff1
  EXPECT_EQ(nova::psci::target_vcpu(0x10000), nova::psci::kInvalidTarget);     // Aff2
  EXPECT_EQ(nova::psci::target_vcpu(0x100000000), nova::psci::kInvalidTarget); // Aff3
}

TEST(PsciModel, Smc64TwinMatchesSmc32) {
  const auto v32 = dispatch(PSCI_FN_AFFINITY_INFO, 0);
  const auto v64 = dispatch(PSCI_FN_AFFINITY_INFO | PSCI_FN_SMC64, 0);
  EXPECT_EQ(v32.ret, v64.ret);
  EXPECT_EQ(v32.action, v64.action);
}

TEST(PsciModel, FeaturesReportsTheImplementedSet) {
  EXPECT_EQ(dispatch(PSCI_FN_FEATURES, PSCI_FN_SYSTEM_RESET).ret, static_cast<std::uint64_t>(PSCI_SUCCESS));
  EXPECT_EQ(dispatch(PSCI_FN_FEATURES, PSCI_FN_VERSION).ret, static_cast<std::uint64_t>(PSCI_SUCCESS));
  EXPECT_EQ(dispatch(PSCI_FN_FEATURES, PSCI_FN_AFFINITY_INFO | PSCI_FN_SMC64).ret,
            static_cast<std::uint64_t>(PSCI_SUCCESS));
  EXPECT_EQ(dispatch(PSCI_FN_FEATURES, PSCI_FN_CPU_ON).ret, static_cast<std::uint64_t>(PSCI_SUCCESS));
  EXPECT_EQ(dispatch(PSCI_FN_FEATURES, PSCI_FN_CPU_OFF).ret, static_cast<std::uint64_t>(PSCI_SUCCESS));
  EXPECT_EQ(dispatch(PSCI_FN_FEATURES, 0x84000001).ret, kNotSupported); // CPU_SUSPEND
  EXPECT_EQ(dispatch(PSCI_FN_FEATURES, 0x12345678).ret, kNotSupported);
}

} // namespace
