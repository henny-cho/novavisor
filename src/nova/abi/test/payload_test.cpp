// Host-side GTest suite for payload validation. The CRC-32 check value
// and the two well-formed layouts are pinned by static_asserts in the
// header; what is tested here is rejection — a corrupted image, and a
// layout broken one field at a time.

#include "nova/abi/payload.hpp"

#include <array>
#include <cstdint>
#include <gtest/gtest.h>
#include <span>

namespace {

TEST(Payload, DetectsACorruptedImage) {
  constexpr std::array<std::uint8_t, 9> bytes{'1', '2', '3', '4', '5', '6', '7', '8', '9'};
  auto                                  corrupted = bytes;
  corrupted[4] ^= 1U;

  constexpr nova::payload::Layout layout{.image_size = bytes.size(), .checksum = 0xCBF43926U};
  EXPECT_TRUE(nova::payload::contents_valid(layout, bytes));
  EXPECT_FALSE(nova::payload::contents_valid(layout, corrupted));
  // A short read is not a checksum failure, but it is not a valid image either.
  EXPECT_FALSE(nova::payload::contents_valid(layout, std::span<const std::uint8_t>{bytes}.first(8)));
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
