// tests/host/esr_parse_test.cpp
//
// Host-side GTest suite for ESR_EL2 parsing utilities (nova/esr.hpp)
// and TrapContext layout assertions (nova/trap_context.hpp).
//
// Depends only on <cstdint> — no bare-metal headers required.

#include "nova/esr.hpp"
#include "nova/trap_context.hpp"

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
// parse_data_abort — Data Abort ISS decoding
// ---------------------------------------------------------------------------

namespace {

// Compose a Data Abort ISS from its fields (the inverse of the parser).
constexpr std::uint64_t make_da_iss(bool isv, unsigned sas, bool sse, unsigned srt, bool sf, bool s1ptw, bool wnr,
                                    unsigned dfsc) {
  return (isv ? (1ULL << 24U) : 0U) | (static_cast<std::uint64_t>(sas) << 22U) | (sse ? (1ULL << 21U) : 0U) |
         (static_cast<std::uint64_t>(srt) << 16U) | (sf ? (1ULL << 15U) : 0U) | (s1ptw ? (1ULL << 7U) : 0U) |
         (wnr ? (1ULL << 6U) : 0U) | dfsc;
}

} // namespace

TEST(ParseDataAbort, Read32BitValidSyndrome) {
  // ldr w3, [x_dev] on an unmapped IPA: ISV=1, SAS=2 (4 bytes), SRT=3,
  // SF=0, WnR=0, DFSC=translation fault level 3.
  const auto da = parse_data_abort(make_da_iss(true, 2, false, 3, false, false, false, 0x07));
  EXPECT_TRUE(da.isv);
  EXPECT_EQ(da.size, 4U);
  EXPECT_FALSE(da.sign_extend);
  EXPECT_EQ(da.srt, 3U);
  EXPECT_FALSE(da.sixty_four);
  EXPECT_FALSE(da.s1ptw);
  EXPECT_FALSE(da.write);
  EXPECT_TRUE(is_translation_fault(da.dfsc));
}

TEST(ParseDataAbort, Write64BitToZeroRegister) {
  // str xzr: SAS=3 (8 bytes), SRT=31, SF=1, WnR=1.
  const auto da = parse_data_abort(make_da_iss(true, 3, false, 31, true, false, true, 0x04));
  EXPECT_EQ(da.size, 8U);
  EXPECT_EQ(da.srt, kSrtZeroReg);
  EXPECT_TRUE(da.sixty_four);
  EXPECT_TRUE(da.write);
}

TEST(ParseDataAbort, InvalidSyndromeAndS1Walk) {
  const auto da = parse_data_abort(make_da_iss(false, 0, false, 0, false, true, false, 0x05));
  EXPECT_FALSE(da.isv);
  EXPECT_TRUE(da.s1ptw);
}

TEST(IsTranslationFault, AllLevelsAndNonTranslation) {
  EXPECT_TRUE(is_translation_fault(0x04));  // level 0
  EXPECT_TRUE(is_translation_fault(0x07));  // level 3
  EXPECT_FALSE(is_translation_fault(0x00)); // address size fault
  EXPECT_FALSE(is_translation_fault(0x0D)); // permission fault level 1
  EXPECT_FALSE(is_translation_fault(0x21)); // alignment fault
}

// ---------------------------------------------------------------------------
// fault_ipa — HPFAR_EL2 page + FAR_EL2 offset composition
// ---------------------------------------------------------------------------

TEST(FaultIpa, ComposesPageAndOffset) {
  // IPA 0x0800_0104: HPFAR holds IPA[51:12] at bits 43:4.
  const std::uint64_t hpfar = (0x08000104ULL >> 12U) << 4U;
  const std::uint64_t far   = 0xFFFF'0000'0000'0104ULL; // VA offset bits only
  EXPECT_EQ(fault_ipa(hpfar, far), 0x08000104ULL);
}

TEST(FaultIpa, IgnoresHpfarReservedBits) {
  const std::uint64_t hpfar = (1ULL << 63U) | ((0x080A0000ULL >> 12U) << 4U) | 0xF;
  EXPECT_EQ(fault_ipa(hpfar, 0), 0x080A0000ULL);
}

// ---------------------------------------------------------------------------
// extend_mmio_read — architectural widening of an emulated load
// ---------------------------------------------------------------------------

TEST(ExtendMmioRead, TruncatesToAccessSize) {
  EXPECT_EQ(extend_mmio_read(0xAABB'CCDDULL, 1, false, false), 0xDDULL);
  EXPECT_EQ(extend_mmio_read(0xAABB'CCDDULL, 2, false, false), 0xCCDDULL);
}

TEST(ExtendMmioRead, SignExtendsToRegisterWidth) {
  // ldrsb w0: byte 0x80 → 0xFFFFFF80 (W register clamps to 32 bits)
  EXPECT_EQ(extend_mmio_read(0x80ULL, 1, true, false), 0xFFFF'FF80ULL);
  // ldrsh x0: halfword 0x8000 → sign-extended through 64 bits
  EXPECT_EQ(extend_mmio_read(0x8000ULL, 2, true, true), 0xFFFF'FFFF'FFFF'8000ULL);
}

TEST(ExtendMmioRead, PositiveValueUnchangedBySse) {
  EXPECT_EQ(extend_mmio_read(0x7FULL, 1, true, true), 0x7FULL);
}

TEST(ExtendMmioRead, FullWidth64Bit) {
  EXPECT_EQ(extend_mmio_read(0x1122'3344'5566'7788ULL, 8, false, true), 0x1122'3344'5566'7788ULL);
}

TEST(ExtendMmioRead, WRegisterClampsWideValue) {
  EXPECT_EQ(extend_mmio_read(0x1122'3344'5566'7788ULL, 8, false, false), 0x5566'7788ULL);
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
