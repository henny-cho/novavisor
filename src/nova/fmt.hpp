#pragma once

// nova/fmt.hpp
//
// Minimal unsigned-integer formatters shared by every component that
// writes diagnostics (trap dumps, HVC exit codes, MMU status lines).
// Pure logic — no I/O, no board dependency — so it is host-testable.
//
// Each formatter renders into a caller-provided buffer and returns a
// string_view over the rendered digits. No printf/itoa, no allocation.

#include <array>
#include <cstddef>
#include <cstdint>
#include <string_view>

namespace nova::fmt {

inline constexpr std::size_t kHexDigits64 = 16; // 64 bits / 4 bits per digit
// Maximum decimal digits of UINT64_MAX (18446744073709551615).
inline constexpr std::size_t kMaxDecDigits64 = 20;

using HexBuf = std::array<char, kHexDigits64>;
using DecBuf = std::array<char, kMaxDecDigits64>;

// Render v as exactly 16 lowercase hex digits (zero-padded, no "0x").
[[nodiscard]] constexpr auto to_hex64(std::uint64_t v, HexBuf& buf) noexcept -> std::string_view {
  constexpr std::string_view kDigits     = "0123456789abcdef";
  constexpr std::uint64_t    kNibbleMask = 0xFU;
  for (std::size_t i = buf.size(); i > 0U; --i) {
    buf[i - 1U] = kDigits[static_cast<std::size_t>(v & kNibbleMask)];
    v >>= 4U;
  }
  return {buf.data(), buf.size()};
}

// Render v in base 10 with no leading zeros ("0" for zero).
[[nodiscard]] constexpr auto to_dec64(std::uint64_t v, DecBuf& buf) noexcept -> std::string_view {
  constexpr std::uint64_t kBase = 10U;
  if (v == 0U) {
    buf[0] = '0';
    return {buf.data(), 1U};
  }
  std::size_t n = 0;
  while (v > 0U && n < buf.size()) {
    buf[buf.size() - 1U - n] = static_cast<char>('0' + static_cast<char>(v % kBase));
    v /= kBase;
    ++n;
  }
  return {buf.data() + (buf.size() - n), n};
}

// Both formatters over the values a diagnostic line actually carries:
// the extremes of the range, and the widths in between.
static_assert(
    [] {
      const auto hex_is = [](std::uint64_t v, std::string_view expected) {
        HexBuf buf{};
        return to_hex64(v, buf) == expected;
      };
      const auto dec_is = [](std::uint64_t v, std::string_view expected) {
        DecBuf buf{};
        return to_dec64(v, buf) == expected;
      };
      return hex_is(0, "0000000000000000") &&                     // always 16 digits, never empty
             hex_is(1, "0000000000000001") &&                     //
             hex_is(0x5000'0000, "0000000050000000") &&           // a load address keeps its leading zeros
             hex_is(0x0123'4567'89AB'CDEF, "0123456789abcdef") && // every nibble value, lowercase
             hex_is(~std::uint64_t{0}, "ffffffffffffffff") &&     //
             dec_is(0, "0") &&                                    // zero is one digit, not none
             dec_is(1, "1") && dec_is(42, "42") &&                //
             dec_is(1'000'000, "1000000") &&                      // no leading zeros above the first digit
             dec_is(~std::uint64_t{0}, "18446744073709551615");   // the widest value the buffer must hold
    }(),
    "the formatters render every value a diagnostic can hand them");

} // namespace nova::fmt
