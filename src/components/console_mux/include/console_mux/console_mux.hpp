#pragma once

// components/console_mux/include/console_mux/console_mux.hpp
//
// Line-level console multiplexer for guest output. The console facade
// serializes per write() call; once two VMs print concurrently their
// fragments interleave mid-line. This layer assembles guest bytes into
// per-vCPU line buffers and emits each completed line as ONE tagged
// facade call ("[vm0] ..."), so lines from different VMs never mix.
//
// Buffers are keyed by vCPU slot, not VM: a slot is written only from
// its affinity core (HVC and vuart MMIO both trap there), which keeps
// every buffer single-writer without a lock. The tag stays per-VM.
//
// Input focus (which VM receives host RX bytes) lives here too, so the
// vuart component can route without owning policy: a focus-switch byte
// cycles across the vuart-carrying VMs.
//
// Hypervisor-own output does not pass through — EL2 log lines keep
// writing the facade directly.

#include <cstddef>
#include <string_view>

namespace nova::console_mux {

// Append guest bytes to the slot's line buffer; every completed line
// leaves as one tagged console write. '\r' is dropped, an over-long
// line is flushed early.
void guest_write(std::size_t slot, std::string_view sv) noexcept;
void guest_putc(std::size_t slot, char c) noexcept;

// Emit whatever the slot has buffered (untagged partial tails would
// otherwise be lost when the vCPU retires).
void flush(std::size_t slot) noexcept;

// Route one host RX byte: returns the focused VM index, or kSwitched
// when the byte was the focus-switch control (consumed here, announced
// with a "[mux] focus vmN" line). Callers inject anything else into
// the returned VM. Single-caller contract: the physical UART IRQ is
// routed to one core.
inline constexpr std::size_t kSwitched  = ~std::size_t{0};
inline constexpr char        kFocusByte = 0x14; // Ctrl-T
[[nodiscard]] auto           input_route(char c) noexcept -> std::size_t;

} // namespace nova::console_mux
