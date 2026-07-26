#include "nova/arch/gicv3_vtr.hpp"

#include <gtest/gtest.h>

namespace {

using namespace nova::arch::gicv3;

// ListRegs [4:0], IDbits [25:23], PREbits [28:26], PRIbits [31:29].
constexpr auto vtr(std::uint64_t list_regs_field, std::uint64_t id_bits, std::uint64_t pre_bits, std::uint64_t pri_bits)
    -> std::uint64_t {
  return list_regs_field | (id_bits << 23U) | (pre_bits << 26U) | (pri_bits << 29U);
}

TEST(GicVtr, DecodesListRegisterCount) {
  EXPECT_EQ(vtr_list_regs(vtr(3, 0, 6, 7)), 4U);
  EXPECT_EQ(vtr_list_regs(vtr(15, 0, 6, 7)), 16U);
}

TEST(GicVtr, IccCtlrMirrorsImplementedBits) {
  // 8 priority bits (field 7), 24-bit INTIDs (field 1) → PRIbits [10:8], IDbits [13:11].
  EXPECT_EQ(icc_ctlr_view(vtr(3, 1, 6, 7)), (1ULL << 11U) | (7ULL << 8U));
  // 5 priority bits (field 4), 16-bit INTIDs (field 0).
  EXPECT_EQ(icc_ctlr_view(vtr(3, 0, 4, 4)), 4ULL << 8U);
}

TEST(GicVtr, VmcrResetRespectsBinaryPointMinimum) {
  // 7 preemption bits (field 6): VBPR0 min = 1, VBPR1 min = 2.
  EXPECT_EQ(vmcr_reset(vtr(3, 0, 6, 7)), kVmcrVpmrAll | kVmcrVeng1 | (1ULL << 21U) | (2ULL << 18U));
  // 8 preemption bits (field 7): both points reach zero-based minimum.
  EXPECT_EQ(vmcr_reset(vtr(3, 0, 7, 7)), kVmcrVpmrAll | kVmcrVeng1 | (0ULL << 21U) | (1ULL << 18U));
  // 5 preemption bits (field 4): VBPR0 min = 3, VBPR1 min = 4.
  EXPECT_EQ(vmcr_reset(vtr(3, 0, 4, 4)), kVmcrVpmrAll | kVmcrVeng1 | (3ULL << 21U) | (4ULL << 18U));
}

} // namespace
