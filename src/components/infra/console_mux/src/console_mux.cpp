// components/console_mux/src/console_mux.cpp
//
// Per-slot line assembly + input focus. Each buffer is single-writer
// (the slot's affinity core), and the final emission is one console
// facade call — the facade's own lock is the only serialization the
// hardware needs. The assembly and cycling rules themselves live in
// line_model.hpp; this TU is the console/guest-table glue.

#include "console_mux/console_mux.hpp"

#include "console_mux/line_model.hpp"
#include "hal/console.hpp"
#include "nova/abi/guest.hpp"
#include "trace/trace.hpp"

#include <array>
#include <cstddef>

namespace nova::console_mux {
namespace {

std::array<LineBuf, kMaxVcpus> g_line;
std::size_t                    g_focus = 0;       // VM receiving host input
LivenessProbe                  g_live  = nullptr; // scheduler-injected

void emit(std::size_t slot) noexcept {
  LineBuf&               l    = g_line[slot];
  const std::string_view line = finish_line(l, vm_of(slot));
  // Hooked here and not in guest_putc: a per-byte hook on the console
  // path would amplify itself, tens of thousands of records deep, every
  // time a guest printed its boot log.
  trace_emit(NOVA_TRACE_EV_UART_LINE, static_cast<std::uint32_t>(slot), l.len);
  console::write(line);
  l.len = 0;
}

// A focus target must carry a vuart and still run its boot vCPU —
// input to anything else would land in a void the guest can never read.
[[nodiscard]] auto focus_valid(std::size_t vm) noexcept -> bool {
  return vm < guest_table().size() && guest_table()[vm].uart == UartKind::kVuart &&
         (g_live == nullptr || g_live(slot_of(vm)));
}

[[nodiscard]] auto cycle_focus(std::size_t from) noexcept -> std::size_t {
  return next_focus(from, guest_table().size(), focus_valid);
}

} // namespace

void guest_putc(std::size_t slot, char c) noexcept {
  if (slot >= kMaxVcpus) {
    return;
  }
  if (put_char(g_line[slot], c)) {
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

void vm_reset(std::size_t vm) noexcept {
  if (vm >= kMaxGuests) {
    return;
  }
  for (std::size_t v = 0; v < kMaxVcpusPerVm; ++v) {
    g_line[slot_of(vm, v)].len = 0;
  }
}

void set_liveness_probe(LivenessProbe probe) noexcept {
  g_live = probe;
}

auto input_route(char c) noexcept -> std::size_t {
  if (c != kFocusByte) {
    // The boot default (VM 0) may not carry a vuart, and the focused VM
    // may have died since — re-route instead of feeding a void.
    if (!focus_valid(g_focus)) {
      g_focus = cycle_focus(g_focus);
    }
    return g_focus;
  }
  g_focus = cycle_focus(g_focus);
  console::line("[mux] focus vm", console::Dec{g_focus}, "\n");
  return kSwitched;
}

} // namespace nova::console_mux
