#include "nova/arch/pci.hpp"

#include <gtest/gtest.h>

namespace {

using nova::arch::pci::Bdf;

TEST(PciAddressing, DerivesRequesterIdFromBdf) {
  constexpr Bdf edu{.bus = 0, .device = 2, .function = 0};
  EXPECT_EQ(nova::arch::pci::requester_id(edu), 0x10);
}

TEST(PciAddressing, LocatesFunctionInEcam) {
  constexpr Bdf edu{.bus = 0, .device = 2, .function = 0};
  EXPECT_EQ(nova::arch::pci::ecam_offset(edu, 0x10), 0x1'0010);
  EXPECT_EQ(0x3F00'0000ULL + nova::arch::pci::ecam_offset(edu, 0), 0x3F01'0000);
}

TEST(PciAddressing, RejectsInvalidFunctionAndRegister) {
  EXPECT_FALSE(nova::arch::pci::valid({.bus = 0, .device = 32, .function = 0}));
  EXPECT_FALSE(nova::arch::pci::valid({.bus = 0, .device = 0, .function = 8}));
  EXPECT_EQ(nova::arch::pci::ecam_offset({.bus = 0, .device = 2, .function = 0}, 4096), 0);
}

} // namespace
