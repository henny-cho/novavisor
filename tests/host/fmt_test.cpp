// tests/host/fmt_test.cpp
//
// Host-side GTest suite for the integer formatters in nova/fmt.hpp.

#include "nova/fmt.hpp"

#include <cstdint>
#include <gtest/gtest.h>
#include <string>

namespace {

auto hex64(std::uint64_t v) -> std::string {
  nova::fmt::HexBuf buf{};
  return std::string{nova::fmt::to_hex64(v, buf)};
}

auto dec64(std::uint64_t v) -> std::string {
  nova::fmt::DecBuf buf{};
  return std::string{nova::fmt::to_dec64(v, buf)};
}

TEST(FmtHex64, ZeroPadsTo16Digits) {
  EXPECT_EQ(hex64(0x0), "0000000000000000");
  EXPECT_EQ(hex64(0x1), "0000000000000001");
  EXPECT_EQ(hex64(0x5000'0000), "0000000050000000");
}

TEST(FmtHex64, AllNibbleValuesLowercase) {
  EXPECT_EQ(hex64(0x0123'4567'89AB'CDEFULL), "0123456789abcdef");
  EXPECT_EQ(hex64(UINT64_MAX), "ffffffffffffffff");
}

TEST(FmtDec64, Zero) {
  EXPECT_EQ(dec64(0), "0");
}

TEST(FmtDec64, NoLeadingZeros) {
  EXPECT_EQ(dec64(1), "1");
  EXPECT_EQ(dec64(42), "42");
  EXPECT_EQ(dec64(1000000), "1000000");
}

TEST(FmtDec64, Max) {
  EXPECT_EQ(dec64(UINT64_MAX), "18446744073709551615");
}

} // namespace
