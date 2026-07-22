// tests/host/esr_parse_test.cpp
//
// Host-side GTest suite for ESR_EL2 parsing utilities (nova/arch/esr.hpp)
// and TrapContext layout assertions (nova/arch/trap_context.hpp).
//
// Depends only on <cstdint> — no bare-metal headers required.

#include "nova/arch/esr.hpp"
#include "nova/arch/sysreg_trap.hpp"
#include "nova/arch/trap_context.hpp"

#include <gtest/gtest.h>

using namespace nova::esr;

// ---------------------------------------------------------------------------
// ExceptionClass enum values
// ---------------------------------------------------------------------------

TEST(EsrExceptionClass, HvcAa64Value) {
  EXPECT_EQ(static_cast<std::uint8_t>(ExceptionClass::HVC_AA64), 0x16U);
}

TEST(EsrExceptionClass, DataAbortLowerValue) {
  EXPECT_EQ(static_cast<std::uint8_t>(ExceptionClass::DATA_ABORT_LOWER), 0x24U);
}

TEST(EsrExceptionClass, InstAbortLowerValue) {
  EXPECT_EQ(static_cast<std::uint8_t>(ExceptionClass::INST_ABORT_LOWER), 0x20U);
}

TEST(EsrExceptionClass, UnknownValue) {
  EXPECT_EQ(static_cast<std::uint8_t>(ExceptionClass::UNKNOWN), 0x00U);
}

// ---------------------------------------------------------------------------
// get_ec — extract EC field (bits 31:26)
// ---------------------------------------------------------------------------

TEST(GetEc, HvcAa64) {
  // EC = 0x16 → bits 31:26 = 0b010110 → shift left 26
  const std::uint64_t esr = static_cast<std::uint64_t>(0x16U) << 26U;
  EXPECT_EQ(get_ec(esr), ExceptionClass::HVC_AA64);
}

TEST(GetEc, DataAbortLower) {
  const std::uint64_t esr = static_cast<std::uint64_t>(0x24U) << 26U;
  EXPECT_EQ(get_ec(esr), ExceptionClass::DATA_ABORT_LOWER);
}

TEST(GetEc, IgnoresLowerBits) {
  // EC = HVC_AA64, ISS = 0xFFFFFF (all lower bits set)
  const std::uint64_t esr = (static_cast<std::uint64_t>(0x16U) << 26U) | 0x01FF'FFFFU;
  EXPECT_EQ(get_ec(esr), ExceptionClass::HVC_AA64);
}

TEST(GetEc, IgnoresUpperBits) {
  // EC = HVC_AA64, upper 32 bits set (RES0 in real HW, but parsing must be robust)
  const std::uint64_t esr = (static_cast<std::uint64_t>(0x16U) << 26U) | (0xFFFF'FFFFULL << 32U);
  EXPECT_EQ(get_ec(esr), ExceptionClass::HVC_AA64);
}

TEST(LowerSyncPolicy, GuestOriginatedClassesAreIsolated) {
  EXPECT_TRUE(is_lower_sync_guest_fault(ExceptionClass::UNKNOWN));
  EXPECT_TRUE(is_lower_sync_guest_fault(ExceptionClass::SVC_AA64));
  EXPECT_TRUE(is_lower_sync_guest_fault(ExceptionClass::SVE));
  EXPECT_TRUE(is_lower_sync_guest_fault(ExceptionClass::INST_ABORT_LOWER));
  EXPECT_TRUE(is_lower_sync_guest_fault(ExceptionClass::PC_ALIGN));
  EXPECT_TRUE(is_lower_sync_guest_fault(ExceptionClass::SP_ALIGN));
  EXPECT_TRUE(is_lower_sync_guest_fault(ExceptionClass::BRKPT_LOWER));
  EXPECT_TRUE(is_lower_sync_guest_fault(ExceptionClass::BRK));
}

TEST(LowerSyncPolicy, HypervisorInvariantClassesStayFatal) {
  EXPECT_FALSE(is_lower_sync_guest_fault(ExceptionClass::INST_ABORT_CURRENT));
  EXPECT_FALSE(is_lower_sync_guest_fault(ExceptionClass::DATA_ABORT_CURRENT));
  EXPECT_FALSE(is_lower_sync_guest_fault(ExceptionClass::BRKPT_CURRENT));
  EXPECT_FALSE(is_lower_sync_guest_fault(ExceptionClass::SOFTSTEP_CURRENT));
  EXPECT_FALSE(is_lower_sync_guest_fault(ExceptionClass::WATCHPT_CURRENT));
  EXPECT_FALSE(is_lower_sync_guest_fault(ExceptionClass::SERROR));
}

// ---------------------------------------------------------------------------
// get_iss — extract ISS field (bits 24:0)
// ---------------------------------------------------------------------------

TEST(GetIss, Zero) {
  EXPECT_EQ(get_iss(0U), 0U);
}

TEST(GetIss, MaxValue) {
  const std::uint64_t esr = 0x01FF'FFFFU; // all 25 ISS bits set
  EXPECT_EQ(get_iss(esr), 0x01FF'FFFFU);
}

TEST(GetIss, IgnoresEcAndIl) {
  // EC=HVC_AA64, IL=1, ISS=0x1234
  const std::uint64_t esr = (static_cast<std::uint64_t>(0x16U) << 26U) | (1U << 25U) | 0x1234U;
  EXPECT_EQ(get_iss(esr), 0x1234U);
}

// ---------------------------------------------------------------------------
// get_hvc_imm — extract HVC immediate (ISS bits 15:0)
// ---------------------------------------------------------------------------

TEST(GetHvcImm, Zero) {
  EXPECT_EQ(get_hvc_imm(0U), 0U);
}

TEST(GetHvcImm, MaxImmediate) {
  // IMM16 = 0xFFFF (lower 16 bits of ISS)
  EXPECT_EQ(get_hvc_imm(0xFFFFU), static_cast<std::uint16_t>(0xFFFFU));
}

TEST(GetHvcImm, TypicalHvcCall) {
  // Typical HVC #1: EC=0x16, IL=1, ISS bits 15:0 = 0x0001
  const std::uint64_t esr = (static_cast<std::uint64_t>(0x16U) << 26U) | (1U << 25U) | 0x0001U;
  EXPECT_EQ(get_hvc_imm(esr), 1U);
}

TEST(GetHvcImm, IgnoresUpperIssBytes) {
  // ISS upper bits set but imm16 = 0x00AB
  const std::uint64_t esr = 0x01FF'00ABU;
  EXPECT_EQ(get_hvc_imm(esr), 0x00ABU);
}

// ---------------------------------------------------------------------------
// is_32bit_instruction — IL bit (bit 25)
// ---------------------------------------------------------------------------

TEST(Is32BitInstruction, SetWhenBit25IsOne) {
  EXPECT_TRUE(is_32bit_instruction(1U << 25U));
}

TEST(Is32BitInstruction, ClearWhenBit25IsZero) {
  EXPECT_FALSE(is_32bit_instruction(0U));
}

// ---------------------------------------------------------------------------
// TrapContext layout (static_asserts are compile-time, but verify sizes too)
// ---------------------------------------------------------------------------

TEST(TrapContextLayout, Size) {
  EXPECT_EQ(sizeof(nova::TrapContext), 288U);
}

TEST(TrapContextLayout, Alignment) {
  EXPECT_EQ(alignof(nova::TrapContext), 16U);
}

TEST(TrapContextLayout, FieldOffsets) {
  EXPECT_EQ(offsetof(nova::TrapContext, x), 0U);
  EXPECT_EQ(offsetof(nova::TrapContext, x) + (30 * sizeof(std::uint64_t)), 240U);
  EXPECT_EQ(offsetof(nova::TrapContext, sp), 248U);
  EXPECT_EQ(offsetof(nova::TrapContext, elr), 256U);
  EXPECT_EQ(offsetof(nova::TrapContext, spsr), 264U);
  EXPECT_EQ(offsetof(nova::TrapContext, esr), 272U);
  EXPECT_EQ(offsetof(nova::TrapContext, far), 280U);
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
