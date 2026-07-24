#include "nova/abi/payload.hpp"

#include <array>
#include <cstdint>
#include <gtest/gtest.h>
#include <span>

namespace {

TEST(Payload, ComputesStandardCrc32) {
  constexpr std::array<std::uint8_t, 9> bytes{'1', '2', '3', '4', '5', '6', '7', '8', '9'};
  static_assert(nova::payload::checksum32(bytes) == 0xCBF43926U);
  EXPECT_EQ(nova::payload::checksum32(bytes), 0xCBF43926U);

  auto corrupted = bytes;
  corrupted[4] ^= 1U;
  EXPECT_NE(nova::payload::checksum32(corrupted), 0xCBF43926U);

  constexpr nova::payload::Layout layout{.image_size = bytes.size(), .checksum = 0xCBF43926U};
  EXPECT_TRUE(nova::payload::contents_valid(layout, bytes));
  EXPECT_FALSE(nova::payload::contents_valid(layout, corrupted));
}

TEST(Payload, AcceptsEmbeddedAndExternalLoaderLayouts) {
  constexpr nova::payload::Layout embedded{
      .source     = 0x40010000,
      .image_size = 0x20000,
      .load_pa    = 0x50000000,
      .ipa_base   = 0x50000000,
      .ipa_size   = 0x100000,
      .entry      = 0x50000000,
      .dtb_ipa    = 0x500F0000,
      .checksum   = 1,
  };
  static_assert(nova::payload::layout_valid(embedded));
  static_assert(nova::payload::layout_valid({}));
}

TEST(Payload, RejectsBoundsAlignmentAndOverlapViolations) {
  nova::payload::Layout layout{
      .source     = 0x40010000,
      .image_size = 0x20000,
      .load_pa    = 0x50000000,
      .ipa_base   = 0x50000000,
      .ipa_size   = 0x100000,
      .entry      = 0x50000000,
      .dtb_ipa    = 0x500F0000,
      .checksum   = 1,
  };

  layout.load_pa += 1;
  EXPECT_FALSE(nova::payload::layout_valid(layout));
  layout.load_pa = 0x50000000;

  layout.image_size = 0xF0001;
  EXPECT_FALSE(nova::payload::layout_valid(layout));
  layout.image_size = 0x20000;

  layout.entry = 0x50100000;
  EXPECT_FALSE(nova::payload::layout_valid(layout));
  layout.entry = 0x50000000;

  layout.source = 0x50000000;
  EXPECT_FALSE(nova::payload::layout_valid(layout));
}

} // namespace
