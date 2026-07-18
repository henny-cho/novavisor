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

} // namespace nova::fmt
