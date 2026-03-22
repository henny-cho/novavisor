// tests/host/esr_parse_test.cpp
//
// Host-side GTest suite for ESR_EL2 parsing utilities (novavisor/esr.hpp)
// and TrapContext layout assertions (novavisor/trap_context.hpp).
//
// Depends only on <cstdint> — no bare-metal headers required.

#include "novavisor/esr.hpp"
#include "novavisor/trap_context.hpp"

#include <gtest/gtest.h>

using namespace novavisor::esr;

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
  EXPECT_EQ(sizeof(novavisor::TrapContext), 288U);
}

TEST(TrapContextLayout, Alignment) {
  EXPECT_EQ(alignof(novavisor::TrapContext), 16U);
}

TEST(TrapContextLayout, FieldOffsets) {
  EXPECT_EQ(offsetof(novavisor::TrapContext, x[0]), 0U);
  EXPECT_EQ(offsetof(novavisor::TrapContext, x[30]), 240U);
  EXPECT_EQ(offsetof(novavisor::TrapContext, sp), 248U);
  EXPECT_EQ(offsetof(novavisor::TrapContext, elr), 256U);
  EXPECT_EQ(offsetof(novavisor::TrapContext, spsr), 264U);
  EXPECT_EQ(offsetof(novavisor::TrapContext, esr), 272U);
  EXPECT_EQ(offsetof(novavisor::TrapContext, far), 280U);
}
