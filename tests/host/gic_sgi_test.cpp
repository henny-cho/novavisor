#include "nova/arch/gicv3_sgi.hpp"

#include <gtest/gtest.h>

namespace {

using nova::arch::gicv3::sgi1r_value;
using nova::arch::gicv3::sgi_target_supported;

TEST(GicSgi, EncodesFlatAffinity) {
  EXPECT_EQ(sgi1r_value(0x0, 5), (5ULL << 24U) | 1ULL);
  EXPECT_EQ(sgi1r_value(0x1, 5), (5ULL << 24U) | 2ULL);
}

TEST(GicSgi, EncodesMultipleAffinityLevels) {
  EXPECT_EQ(sgi1r_value(0x00000100, 7), (7ULL << 24U) | (1ULL << 16U) | 1ULL);
  EXPECT_EQ(sgi1r_value(0x00010000, 7), (1ULL << 32U) | (7ULL << 24U) | 1ULL);
  EXPECT_EQ(sgi1r_value(0x0100010102, 7), (1ULL << 48U) | (1ULL << 32U) | (7ULL << 24U) | (1ULL << 16U) | (1ULL << 2U));
}

TEST(GicSgi, IdentifiesRangeSelectorRequirement) {
  EXPECT_TRUE(sgi_target_supported(0x0F));
  EXPECT_FALSE(sgi_target_supported(0x10));
}

} // namespace
