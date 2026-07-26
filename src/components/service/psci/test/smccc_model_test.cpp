#include "psci/psci_model.hpp"
#include "psci/smccc_model.hpp"

#include <gtest/gtest.h>

namespace {

TEST(SmcccModel, ClaimsTheWholeArchRange) {
  EXPECT_TRUE(nova::smccc::dispatch(SMCCC_FN_VERSION, 0).claimed);
  EXPECT_TRUE(nova::smccc::dispatch(0x8000ABCD, 0).claimed);
  EXPECT_FALSE(nova::smccc::dispatch(PSCI_FN_VERSION, 0).claimed);
  EXPECT_FALSE(nova::smccc::dispatch(0x1000, 0).claimed);
}

TEST(SmcccModel, ReportsVersionOnePointOne) {
  EXPECT_EQ(nova::smccc::dispatch(SMCCC_FN_VERSION, 0).ret, std::uint64_t{SMCCC_VERSION_1_1});
}

TEST(SmcccModel, ArchFeaturesAnswersPerFunction) {
  EXPECT_EQ(nova::smccc::dispatch(SMCCC_FN_ARCH_FEATURES, SMCCC_FN_VERSION).ret, std::uint64_t{SMCCC_SUCCESS});
  EXPECT_EQ(nova::smccc::dispatch(SMCCC_FN_ARCH_FEATURES, SMCCC_FN_WORKAROUND_1).ret, std::uint64_t{SMCCC_SUCCESS});
  EXPECT_EQ(nova::smccc::dispatch(SMCCC_FN_ARCH_FEATURES, SMCCC_FN_ARCH_SOC_ID).ret,
            static_cast<std::uint64_t>(SMCCC_NOT_SUPPORTED));
}

TEST(SmcccModel, WorkaroundsAreDiscoverableAndNotRequired) {
  for (const std::uint32_t fid : {SMCCC_FN_WORKAROUND_1, SMCCC_FN_WORKAROUND_2, SMCCC_FN_WORKAROUND_3}) {
    const auto v = nova::smccc::dispatch(fid, 0);
    EXPECT_TRUE(v.claimed);
    EXPECT_EQ(v.ret, static_cast<std::uint64_t>(SMCCC_NOT_REQUIRED));
  }
}

TEST(SmcccModel, UnknownArchIdIsClaimedNotSupported) {
  const auto v = nova::smccc::dispatch(0x80001234, 0);
  EXPECT_TRUE(v.claimed);
  EXPECT_EQ(v.ret, static_cast<std::uint64_t>(SMCCC_NOT_SUPPORTED));
}

// The gate guest Linux uses to enable every SMCCC 1.1 call, including
// the Spectre-v2 / BHB firmware mitigations.
TEST(SmcccModel, PsciFeaturesReportsSmcccVersionPresent) {
  EXPECT_EQ(nova::psci::dispatch(PSCI_FN_FEATURES, SMCCC_FN_VERSION).ret, std::uint64_t{PSCI_SUCCESS});
  EXPECT_EQ(nova::psci::dispatch(PSCI_FN_FEATURES, SMCCC_FN_WORKAROUND_1).ret, std::uint64_t{PSCI_SUCCESS});
  EXPECT_EQ(nova::psci::dispatch(PSCI_FN_FEATURES, SMCCC_FN_ARCH_SOC_ID).ret,
            static_cast<std::uint64_t>(PSCI_NOT_SUPPORTED));
}

} // namespace
