#pragma once

// hal/console.hpp
//
// Board-agnostic console facade. This is the ONE place where generic code
// binds to the active board's UART: components include this header — never
// a hal/board_*/ one — so porting to a new board touches only this file
// (and the new board driver).

#include "hal/board/qemu_virt/include/uart.hpp"
#include "nova/fmt.hpp"
#include "nova/sync.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <string_view>

namespace nova::console {

// The UART is one shared FIFO. Use write_parts for atomic multi-fragment lines.
inline sync::SpinLock g_lock;

inline void write(std::string_view sv) noexcept {
  sync::Guard guard{g_lock};
  board::qemu_virt::uart_write(sv);
}

// Null-terminated C strings (extern "C" boundaries, __func__-style values).
inline void write(const char* str) noexcept {
  sync::Guard guard{g_lock};
  board::qemu_virt::uart_puts(str);
}

// Emit one logical line from preformatted fragments under one lock.
template <std::size_t N>
inline void write_parts(const std::array<std::string_view, N>& parts) noexcept {
  sync::Guard guard{g_lock};
  for (const std::string_view part : parts) {
    board::qemu_virt::uart_write(part);
  }
}

// 16 zero-padded lowercase hex digits, no "0x" prefix.
inline void write_hex64(std::uint64_t v) noexcept {
  fmt::HexBuf buf{};
  write(fmt::to_hex64(v, buf));
}

// Base 10, no leading zeros.
inline void write_dec64(std::uint64_t v) noexcept {
  fmt::DecBuf buf{};
  write(fmt::to_dec64(v, buf));
}

// Host input: one RX byte, or -1 when none is waiting. Single consumer
// by construction (the UART interrupt is routed to one core), so the
// write lock is not involved — RX and TX are separate FIFOs.
[[nodiscard]] inline auto try_read() noexcept -> int {
  return board::qemu_virt::uart_try_read();
}

// Unmask the console's RX interrupt at the device. GIC routing of the
// UART SPI stays with the caller (hal/gic.hpp enable_spi).
inline void rx_irq_enable() noexcept {
  board::qemu_virt::uart_rx_irq_enable();
}

} // namespace nova::console
