// Host-side GTest suite for the console multiplexer's pure logic
// (components/console_mux/include/console_mux/line_model.hpp): line
// assembly, tag rendering and input-focus cycling.

#include "console_mux/line_model.hpp"

#include <cstddef>
#include <gtest/gtest.h>
#include <string>
#include <string_view>

namespace {

using nova::console_mux::kLineMax;
using nova::console_mux::kTagLen;
using nova::console_mux::LineBuf;
using nova::console_mux::next_focus;
using nova::console_mux::put_char;
using nova::console_mux::render_tag;

// What the component would hand the console facade for a completed
// line: tag + payload + '\n'.
auto completed_line(LineBuf& l, std::size_t vm) -> std::string {
  render_tag(l, vm);
  l.data[kTagLen + l.len] = '\n';
  return std::string{std::string_view{l.data.data(), kTagLen + l.len + 1}};
}

// Feed a string; returns how many times the caller would have emitted.
auto feed(LineBuf& l, std::string_view sv) -> int {
  int emits = 0;
  for (const char c : sv) {
    if (put_char(l, c)) {
      ++emits;
      l.len = 0; // emit() resets the payload
    }
  }
  return emits;
}

// ---------------------------------------------------------------------------
// Line assembly
// ---------------------------------------------------------------------------

TEST(ConsoleMuxLine, NewlineCompletesTaggedLine) {
  LineBuf l{};
  EXPECT_FALSE(put_char(l, 'h'));
  EXPECT_FALSE(put_char(l, 'i'));
  EXPECT_TRUE(put_char(l, '\n'));
  EXPECT_EQ(l.len, 2U);
  EXPECT_EQ(completed_line(l, 1), "[vm1] hi\n");
}

TEST(ConsoleMuxLine, EmptyLineStillEmits) {
  LineBuf l{};
  EXPECT_TRUE(put_char(l, '\n'));
  EXPECT_EQ(l.len, 0U);
  EXPECT_EQ(completed_line(l, 0), "[vm0] \n");
}

TEST(ConsoleMuxLine, CarriageReturnDropped) {
  LineBuf l{};
  EXPECT_EQ(feed(l, "a\r"), 0);
  EXPECT_EQ(l.len, 1U);       // '\r' consumed no payload byte
  EXPECT_EQ(feed(l, "b"), 0); // and did not complete the line
  EXPECT_EQ(l.len, 2U);
  EXPECT_EQ(feed(l, "\r\n"), 1); // CRLF emits exactly once
}

TEST(ConsoleMuxLine, EarlyFlushAtLineMax) {
  LineBuf l{};
  EXPECT_EQ(feed(l, std::string(kLineMax - 1, 'x')), 0);
  EXPECT_TRUE(put_char(l, 'x')); // the kLineMax-th byte forces the flush
  EXPECT_EQ(l.len, kLineMax);
  EXPECT_EQ(completed_line(l, 2).size(), kTagLen + kLineMax + 1);
  l.len = 0;

  // The flush is per line, not per buffer: the next payload starts over.
  EXPECT_EQ(feed(l, std::string(2 * kLineMax, 'y')), 2);
}

TEST(ConsoleMuxLine, PayloadNeverOverrunsBuffer) {
  LineBuf l{};
  // Worst case: a full payload plus the '\n' the emitter appends.
  EXPECT_EQ(kTagLen + kLineMax + 1, l.data.size());
}

// ---------------------------------------------------------------------------
// Tag rendering
// ---------------------------------------------------------------------------

TEST(ConsoleMuxTag, RendersEveryVmIndex) {
  for (std::size_t vm = 0; vm < 4; ++vm) {
    LineBuf l{};
    render_tag(l, vm);
    EXPECT_EQ(std::string_view(l.data.data(), kTagLen), "[vm" + std::to_string(vm) + "] ");
  }
}

// ---------------------------------------------------------------------------
// Focus cycling
// ---------------------------------------------------------------------------

TEST(ConsoleMuxFocus, WrapsPastTheLastIndex) {
  const auto all = [](std::size_t) { return true; };
  EXPECT_EQ(next_focus(0, 3, all), 1U);
  EXPECT_EQ(next_focus(1, 3, all), 2U);
  EXPECT_EQ(next_focus(2, 3, all), 0U);
}

TEST(ConsoleMuxFocus, SkipsInvalidTargets) {
  const auto only_two = [](std::size_t vm) { return vm == 2; };
  EXPECT_EQ(next_focus(0, 4, only_two), 2U);
  EXPECT_EQ(next_focus(3, 4, only_two), 2U); // wraps forward to find it
  EXPECT_EQ(next_focus(2, 4, only_two), 2U); // full turn back to itself
}

TEST(ConsoleMuxFocus, KeepsCurrentWhenNoneValid) {
  const auto none = [](std::size_t) { return false; };
  EXPECT_EQ(next_focus(1, 4, none), 1U);
}

TEST(ConsoleMuxFocus, SingleTargetStaysPut) {
  const auto all = [](std::size_t) { return true; };
  EXPECT_EQ(next_focus(0, 1, all), 0U);
}

} // namespace
