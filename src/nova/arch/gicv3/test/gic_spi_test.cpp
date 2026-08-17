// Host-side GTest suite for the SPI selection boundaries. The bank
// arithmetic itself is pinned by a static_assert in the header; what is
// tested here is which INTIDs a distributor will accept at all.

#include "nova/arch/gicv3/spi.hpp"

#include <gtest/gtest.h>

namespace {

using nova::arch::gicv3::spi_registers;

TEST(GicSpi, RejectsPrivateAndSpecialIntids) {
  EXPECT_FALSE(spi_registers(0).valid);
  EXPECT_FALSE(spi_registers(31).valid);
  EXPECT_TRUE(spi_registers(1019).valid);
  EXPECT_FALSE(spi_registers(1020).valid);
  EXPECT_FALSE(spi_registers(UINT32_MAX).valid);
}

TEST(GicSpi, LimitsSelectionToImplementedIntids) {
  EXPECT_EQ(nova::arch::gicv3::implemented_intids(0), 32);
  EXPECT_FALSE(nova::arch::gicv3::spi_implemented(32, 0));

  EXPECT_EQ(nova::arch::gicv3::implemented_intids(1), 64);
  EXPECT_TRUE(nova::arch::gicv3::spi_implemented(63, 1));
  EXPECT_FALSE(nova::arch::gicv3::spi_implemented(64, 1));

  EXPECT_EQ(nova::arch::gicv3::implemented_intids(3), 128);
  EXPECT_TRUE(nova::arch::gicv3::spi_implemented(109, 3));
  EXPECT_EQ(nova::arch::gicv3::implemented_intids(31), 1020);
}

} // namespace
