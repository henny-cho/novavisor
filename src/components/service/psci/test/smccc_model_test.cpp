#include "psci/psci_model.hpp"
#include "psci/smccc_model.hpp"

#include <gtest/gtest.h>

namespace {

using nova::arch::SpeculationState;

// A PE that reports every relevant property, and one that discloses
// nothing — the two ends the workaround answers must distinguish.
constexpr SpeculationState kSafePe{.csv2 = 3, .csv2_frac = 0, .csv3 = 1, .ssbs = 2, .clrbhb = true};
constexpr SpeculationState kSilentPe{};

TEST(SmcccModel, ClaimsTheWholeArchRange) {
  EXPECT_TRUE(nova::smccc::dispatch(SMCCC_FN_VERSION, 0, kSafePe).claimed);
  EXPECT_TRUE(nova::smccc::dispatch(0x8000ABCD, 0, kSafePe).claimed);
  EXPECT_FALSE(nova::smccc::dispatch(PSCI_FN_VERSION, 0, kSafePe).claimed);
  EXPECT_FALSE(nova::smccc::dispatch(0x1000, 0, kSafePe).claimed);
}

TEST(SmcccModel, ReportsVersionOnePointOne) {
  EXPECT_EQ(nova::smccc::dispatch(SMCCC_FN_VERSION, 0, kSilentPe).ret, std::uint64_t{SMCCC_VERSION_1_1});
}

TEST(SmcccModel, ArchFeaturesAnswersPerFunction) {
  EXPECT_EQ(nova::smccc::dispatch(SMCCC_FN_ARCH_FEATURES, SMCCC_FN_VERSION, kSafePe).ret, std::uint64_t{SMCCC_SUCCESS});
  EXPECT_EQ(nova::smccc::dispatch(SMCCC_FN_ARCH_FEATURES, SMCCC_FN_ARCH_SOC_ID, kSafePe).ret,
            static_cast<std::uint64_t>(SMCCC_NOT_SUPPORTED));
}

// Discovery must not promise something the call then refuses, so
// ARCH_FEATURES routes a workaround ID through the same verdict.
TEST(SmcccModel, ArchFeaturesMatchesTheWorkaroundItDescribes) {
  for (const std::uint32_t fid : {SMCCC_FN_WORKAROUND_1, SMCCC_FN_WORKAROUND_2, SMCCC_FN_WORKAROUND_3}) {
    for (const SpeculationState& pe : {kSafePe, kSilentPe}) {
      EXPECT_EQ(nova::smccc::dispatch(SMCCC_FN_ARCH_FEATURES, fid, pe).ret, nova::smccc::dispatch(fid, 0, pe).ret);
    }
  }
}

// NOT_REQUIRED asserts the PE is unaffected — only a PE that reports the
// property earns it.
TEST(SmcccModel, WorkaroundsAreNotRequiredOnlyWithEvidence) {
  for (const std::uint32_t fid : {SMCCC_FN_WORKAROUND_1, SMCCC_FN_WORKAROUND_2, SMCCC_FN_WORKAROUND_3}) {
    const auto v = nova::smccc::dispatch(fid, 0, kSafePe);
    EXPECT_TRUE(v.claimed);
    EXPECT_EQ(v.ret, static_cast<std::uint64_t>(SMCCC_NOT_REQUIRED));
  }
}

// The defect this binding closes: an undisclosed PE used to be told
// "unaffected", which stops the guest from mitigating.
TEST(SmcccModel, UndisclosedPeGetsNotSupportedNotNotRequired) {
  for (const std::uint32_t fid : {SMCCC_FN_WORKAROUND_1, SMCCC_FN_WORKAROUND_2, SMCCC_FN_WORKAROUND_3}) {
    const auto v = nova::smccc::dispatch(fid, 0, kSilentPe);
    EXPECT_TRUE(v.claimed);
    EXPECT_EQ(v.ret, static_cast<std::uint64_t>(SMCCC_NOT_SUPPORTED));
  }
}

// CLRBHB lets the guest clear the history itself; the PE stays affected,
// so the branch-history call must not claim otherwise.
TEST(SmcccModel, GuestSideBhbMitigationIsNotAnUnaffectedPe) {
  constexpr SpeculationState kClrbhbOnly{.csv2 = 1, .csv2_frac = 1, .csv3 = 1, .ssbs = 2, .clrbhb = true};
  EXPECT_EQ(nova::smccc::dispatch(SMCCC_FN_WORKAROUND_3, 0, kClrbhbOnly).ret,
            static_cast<std::uint64_t>(SMCCC_NOT_SUPPORTED));
  // Branch targets are still covered by CSV2 == 1.
  EXPECT_EQ(nova::smccc::dispatch(SMCCC_FN_WORKAROUND_1, 0, kClrbhbOnly).ret,
            static_cast<std::uint64_t>(SMCCC_NOT_REQUIRED));
}

TEST(SmcccModel, UnknownArchIdIsClaimedNotSupported) {
  const auto v = nova::smccc::dispatch(0x80001234, 0, kSafePe);
  EXPECT_TRUE(v.claimed);
  EXPECT_EQ(v.ret, static_cast<std::uint64_t>(SMCCC_NOT_SUPPORTED));
}

// The gate guest Linux uses to enable every SMCCC 1.1 call, including
// the Spectre-v2 / BHB firmware mitigations. Discoverability of the
// workaround IDs is independent of the per-PE verdict above.
TEST(SmcccModel, PsciFeaturesReportsSmcccVersionPresent) {
  EXPECT_EQ(nova::psci::dispatch(PSCI_FN_FEATURES, SMCCC_FN_VERSION).ret, std::uint64_t{PSCI_SUCCESS});
  EXPECT_EQ(nova::psci::dispatch(PSCI_FN_FEATURES, SMCCC_FN_WORKAROUND_1).ret, std::uint64_t{PSCI_SUCCESS});
  EXPECT_EQ(nova::psci::dispatch(PSCI_FN_FEATURES, SMCCC_FN_ARCH_SOC_ID).ret,
            static_cast<std::uint64_t>(PSCI_NOT_SUPPORTED));
}

} // namespace
