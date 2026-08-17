// Host-side GTest suite for the ESR_EL2 routing policy and the trapped
// MSR/MRS syndrome matchers. Field extraction and the TrapContext
// layout are pinned by static_asserts in their own headers, so every
// build checks them; what remains here is the policy each syndrome
// feeds.
//
// Depends only on <cstdint> — no bare-metal headers required.

#include "nova/arch/esr.hpp"
#include "nova/arch/sysreg_trap.hpp"

#include <gtest/gtest.h>

using namespace nova::esr;

TEST(LowerSyncPolicy, GuestOriginatedClassesAreIsolated) {
  EXPECT_TRUE(is_lower_sync_guest_fault(ExceptionClass::kUnknown));
  EXPECT_TRUE(is_lower_sync_guest_fault(ExceptionClass::kSvcAa64));
  EXPECT_TRUE(is_lower_sync_guest_fault(ExceptionClass::kSve));
  EXPECT_TRUE(is_lower_sync_guest_fault(ExceptionClass::kInstAbortLower));
  EXPECT_TRUE(is_lower_sync_guest_fault(ExceptionClass::kPcAlign));
  EXPECT_TRUE(is_lower_sync_guest_fault(ExceptionClass::kSpAlign));
  EXPECT_TRUE(is_lower_sync_guest_fault(ExceptionClass::kBrkptLower));
  EXPECT_TRUE(is_lower_sync_guest_fault(ExceptionClass::kBrk));
}

TEST(LowerSyncPolicy, HypervisorInvariantClassesStayFatal) {
  EXPECT_FALSE(is_lower_sync_guest_fault(ExceptionClass::kInstAbortCurrent));
  EXPECT_FALSE(is_lower_sync_guest_fault(ExceptionClass::kDataAbortCurrent));
  EXPECT_FALSE(is_lower_sync_guest_fault(ExceptionClass::kBrkptCurrent));
  EXPECT_FALSE(is_lower_sync_guest_fault(ExceptionClass::kSoftstepCurrent));
  EXPECT_FALSE(is_lower_sync_guest_fault(ExceptionClass::kWatchptCurrent));
  EXPECT_FALSE(is_lower_sync_guest_fault(ExceptionClass::kSerror));
}

// ---------------------------------------------------------------------------
// Trapped MSR/MRS syndrome (EC 0x18)
// ---------------------------------------------------------------------------

namespace {

// Build an EC 0x18 ISS from the register tuple (Direction 0 = write).
constexpr auto sysreg_iss(std::uint64_t op0, std::uint64_t op1, std::uint64_t crn, std::uint64_t crm, std::uint64_t op2,
                          std::uint64_t rt, bool read) -> std::uint64_t {
  return (op0 << 20U) | (op2 << 17U) | (op1 << 14U) | (crn << 10U) | (rt << 5U) | (crm << 1U) | (read ? 1U : 0U);
}

} // namespace

TEST(ParseSysregTrap, DecodesIccSgi1rWrite) {
  // ICC_SGI1R_EL1 = S3_0_C12_C11_5, written through x7.
  const auto s = nova::esr::parse_sysreg_trap(sysreg_iss(3, 0, 12, 11, 5, 7, /*read=*/false));
  EXPECT_TRUE(nova::esr::is_icc_sgi1r(s));
  EXPECT_TRUE(s.write);
  EXPECT_EQ(s.rt, 7U);
}

TEST(ParseSysregTrap, ReadDirectionAndOtherRegistersRejected) {
  const auto read = nova::esr::parse_sysreg_trap(sysreg_iss(3, 0, 12, 11, 5, 7, /*read=*/true));
  EXPECT_FALSE(read.write);
  EXPECT_TRUE(nova::esr::is_icc_sgi1r(read)); // matcher is tuple-only; direction is the caller's test

  // ICC_ASGI1R_EL1 (op2=6) is a different register.
  const auto asgi = nova::esr::parse_sysreg_trap(sysreg_iss(3, 0, 12, 11, 6, 7, false));
  EXPECT_FALSE(nova::esr::is_icc_sgi1r(asgi));
}

TEST(ParseSysregTrap, RtThirtyOneIsZeroRegister) {
  const auto s = nova::esr::parse_sysreg_trap(sysreg_iss(3, 0, 12, 11, 5, 31, false));
  EXPECT_EQ(s.rt, 31U);
}

TEST(ParseSysregTrap, MatchesCntpEl1TimerBank) {
  // CNTP_TVAL/CTL/CVAL_EL0 = S3_3_C14_C2_{0,1,2}, reads and writes alike.
  for (std::uint64_t op2 = 0; op2 <= 2; ++op2) {
    EXPECT_TRUE(nova::esr::is_cntp_el1_timer(nova::esr::parse_sysreg_trap(sysreg_iss(3, 3, 14, 2, op2, 1, false))));
    EXPECT_TRUE(nova::esr::is_cntp_el1_timer(nova::esr::parse_sysreg_trap(sysreg_iss(3, 3, 14, 2, op2, 1, true))));
  }
}

TEST(ParseSysregTrap, CntvAndCounterRegistersAreNotCntp) {
  // CNTV_CTL_EL0 (S3_3_C14_C3_1) is the guest's live timer — never claimed.
  EXPECT_FALSE(nova::esr::is_cntp_el1_timer(nova::esr::parse_sysreg_trap(sysreg_iss(3, 3, 14, 3, 1, 1, false))));
  // CNTPCT_EL0 (S3_3_C14_C0_1) is the counter, not the timer bank.
  EXPECT_FALSE(nova::esr::is_cntp_el1_timer(nova::esr::parse_sysreg_trap(sysreg_iss(3, 3, 14, 0, 1, 1, true))));
}
