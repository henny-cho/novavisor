#include "nova/arch/gicv3_spi.hpp"

#include <gtest/gtest.h>

namespace {

using nova::arch::gicv3::spi_registers;

TEST(GicSpi, SelectsFirstSharedBankBoundaries) {
  auto regs = spi_registers(32);
  ASSERT_TRUE(regs.valid);
  EXPECT_EQ(regs.bit, 1U);
  EXPECT_EQ(regs.group_offset, 0x0084);
  EXPECT_EQ(regs.enable_offset, 0x0104);
  EXPECT_EQ(regs.disable_offset, 0x0184);
  EXPECT_EQ(regs.clear_offset, 0x0284);
  EXPECT_EQ(regs.config_offset, 0x0C08);
  EXPECT_EQ(regs.edge_bit, 1U << 1U);
  EXPECT_EQ(regs.route_offset, 0x6100);

  regs = spi_registers(63);
  ASSERT_TRUE(regs.valid);
  EXPECT_EQ(regs.bit, 1U << 31U);
  EXPECT_EQ(regs.enable_offset, 0x0104);
  EXPECT_EQ(regs.route_offset, 0x61F8);

  regs = spi_registers(64);
  ASSERT_TRUE(regs.valid);
  EXPECT_EQ(regs.bit, 1U);
  EXPECT_EQ(regs.group_offset, 0x0088);
  EXPECT_EQ(regs.enable_offset, 0x0108);
  EXPECT_EQ(regs.config_offset, 0x0C10);
  EXPECT_EQ(regs.edge_bit, 1U << 1U);
  EXPECT_EQ(regs.route_offset, 0x6200);
}

TEST(GicSpi, SelectsSmmuInterruptBanks) {
  constexpr std::uint32_t kFirstSmmuIntid = 106;
  constexpr std::uint32_t kLastSmmuIntid  = 109;

  const auto first = spi_registers(kFirstSmmuIntid);
  ASSERT_TRUE(first.valid);
  EXPECT_EQ(first.bit, 1U << 10U);
  EXPECT_EQ(first.group_offset, 0x008C);
  EXPECT_EQ(first.enable_offset, 0x010C);
  EXPECT_EQ(first.disable_offset, 0x018C);
  EXPECT_EQ(first.clear_offset, 0x028C);
  EXPECT_EQ(first.config_offset, 0x0C18);
  EXPECT_EQ(first.edge_bit, 1U << 21U);
  EXPECT_EQ(first.route_offset, 0x6350);

  const auto last = spi_registers(kLastSmmuIntid);
  ASSERT_TRUE(last.valid);
  EXPECT_EQ(last.bit, 1U << 13U);
  EXPECT_EQ(last.group_offset, first.group_offset);
  EXPECT_EQ(last.enable_offset, first.enable_offset);
  EXPECT_EQ(last.config_offset, first.config_offset);
  EXPECT_EQ(last.edge_bit, 1U << 27U);
  EXPECT_EQ(last.route_offset, 0x6368);
}

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
