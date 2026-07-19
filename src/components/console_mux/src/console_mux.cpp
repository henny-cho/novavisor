// components/console_mux/src/console_mux.cpp
//
// Per-slot line assembly + input focus. Each buffer is single-writer
// (the slot's affinity core), and the final emission is one console
// facade call — the facade's own lock is the only serialization the
// hardware needs.

#include "console_mux/console_mux.hpp"

#include "hal/console.hpp"
#include "nova/abi/guest.hpp"

#include <array>
#include <cstddef>

namespace nova::console_mux {
namespace {

inline constexpr std::size_t kTagLen  = 6;   // "[vmN] "
inline constexpr std::size_t kLineMax = 120; // early flush past this

struct LineBuf {
  std::array<char, kTagLen + kLineMax + 1> data{}; // tag + payload + '\n'
  std::size_t                              len = 0;
};

std::array<LineBuf, kMaxVcpus> g_line;
std::size_t                    g_focus = 0; // VM receiving host input

void emit(std::size_t slot) noexcept {
  LineBuf& l = g_line[slot];
  l.data[0]  = '[';
  l.data[1]  = 'v';
  l.data[2]  = 'm';
  l.data[3]  = static_cast<char>('0' + vm_of(slot));
  l.data[4]  = ']';
  l.data[5]  = ' ';

  l.data[kTagLen + l.len] = '\n';
  console::write(std::string_view{l.data.data(), kTagLen + l.len + 1});
  l.len = 0;
}

} // namespace

void guest_putc(std::size_t slot, char c) noexcept {
  if (slot >= kMaxVcpus || c == '\r') {
    return;
  }
  if (c == '\n') {
    emit(slot);
    return;
  }
  LineBuf& l                = g_line[slot];
  l.data[kTagLen + l.len++] = c;
  if (l.len == kLineMax) {
    emit(slot);
  }
}

void guest_write(std::size_t slot, std::string_view sv) noexcept {
  for (const char c : sv) {
    guest_putc(slot, c);
  }
}

void flush(std::size_t slot) noexcept {
  if (slot < kMaxVcpus && g_line[slot].len != 0) {
    emit(slot);
  }
}

auto input_route(char c) noexcept -> std::size_t {
  if (c != kFocusByte) {
    return g_focus;
  }
  g_focus = (g_focus + 1) % guest_table().size();
  console::write("[mux] focus vm");
  console::write_dec64(g_focus);
  console::write("\n");
  return kSwitched;
}

} // namespace nova::console_mux
