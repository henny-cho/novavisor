// tests/host/data_abort_test.cpp
//
// Host-side GTest suite for the Data Abort / MMIO decode utilities
// (nova/arch/data_abort.hpp).
//
// Depends only on <cstdint> — no bare-metal headers required.

#include "nova/arch/data_abort.hpp"

#include <gtest/gtest.h>

using namespace nova::esr;

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
