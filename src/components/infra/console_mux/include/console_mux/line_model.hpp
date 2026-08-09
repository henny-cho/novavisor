#pragma once

// components/console_mux/include/console_mux/line_model.hpp
//
// Pure line-assembly and focus-cycling logic, host-testable. The
// component keeps only the emission glue (console facade, buffer array,
// focus state) so the rules that decide WHEN a line leaves and WHICH VM
// owns the input can be pinned without a console or a guest table.

#include "nova/abi/guest.hpp"

#include <array>
#include <cstddef>
#include <string_view>

namespace nova::console_mux {

inline constexpr std::size_t kTagLen  = 6;   // "[vmN] "
inline constexpr std::size_t kLineMax = 120; // early flush past this

struct LineBuf {
  std::array<char, kTagLen + kLineMax + 1> data{}; // tag + payload + '\n'
  std::size_t                              len = 0;
};

// Stamp the per-VM tag into the reserved prefix. A fixed kTagLen is what
// lets the payload be written at a constant offset from byte 0, so the
// VM index must stay a single digit.
static_assert(kMaxGuests <= 10, "the [vmN] tag holds one digit");

constexpr void render_tag(LineBuf& l, std::size_t vm) noexcept {
  l.data[0] = '[';
  l.data[1] = 'v';
  l.data[2] = 'm';
  l.data[3] = static_cast<char>('0' + vm);
  l.data[4] = ']';
  l.data[5] = ' ';
}

// Accumulate one guest byte; true when the line is complete and the
// caller must emit it. '\r' is dropped (a CRLF guest would otherwise
// print an extra blank line through the tagged path), '\n' terminates
// the line — including an empty one — and a payload reaching kLineMax
// flushes early, so a guest that never sends a newline still reaches
// the console.
[[nodiscard]] constexpr auto put_char(LineBuf& l, char c) noexcept -> bool {
  if (c == '\r') {
    return false;
  }
  if (c == '\n') {
    return true;
  }
  // A full payload keeps saying "emit" rather than writing past itself,
  // so the reset the caller owes is a matter of losing no bytes, not of
  // staying inside the buffer.
  if (l.len < kLineMax) {
    l.data[kTagLen + l.len++] = c;
  }
  return l.len == kLineMax;
}

// The exact bytes the console is handed for a completed line: the tag,
// the payload, then the newline that ends it. Stamped here so that
// where a line ends is said once.
[[nodiscard]] constexpr auto finish_line(LineBuf& l, std::size_t vm) noexcept -> std::string_view {
  render_tag(l, vm);
  l.data[kTagLen + l.len] = '\n';
  return std::string_view{l.data.data(), kTagLen + l.len + 1};
}

// First index after `from` (wrapping over `count`) that `valid` accepts;
// `from` itself when none does — the caller keeps its current focus
// rather than routing input into a void.
template <typename Pred>
[[nodiscard]] constexpr auto next_focus(std::size_t from, std::size_t count, Pred&& valid) -> std::size_t {
  for (std::size_t i = 1; i <= count; ++i) {
    const std::size_t vm = (from + i) % count;
    if (valid(vm)) {
      return vm;
    }
  }
  return from;
}

} // namespace nova::console_mux
