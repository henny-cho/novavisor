#pragma once

#include "board.hpp"

#include <cstdint>
#include <span>
#include <string_view>

namespace nova::board::qemu_virt {

// PL011 register offsets and flags (ARM DDI0183, §3.2).
inline constexpr uintptr_t kUartFrOffset = 0x18;    // Flag Register
inline constexpr uint32_t  kUartFrTxFull = 1U << 5; // FR.TXFF — TX FIFO full

// Primary write primitive: all other overloads delegate here.
// Polls FR.TXFF before each byte so nothing is dropped when the TX FIFO
// fills — QEMU never reports a full FIFO, but real PL011 hardware does.
inline void uart_write(std::span<const uint8_t> data) noexcept {
  auto* const       dr = reinterpret_cast<volatile uint32_t*>(UART0_BASE);
  const auto* const fr = reinterpret_cast<const volatile uint32_t*>(UART0_BASE + kUartFrOffset);
  for (const auto byte : data) {
    while ((*fr & kUartFrTxFull) != 0U) {
      // busy-wait for TX FIFO space
    }
    *dr = static_cast<uint32_t>(byte);
  }
}

// Write a string_view to UART0. Length is known; no null-termination required.
inline void uart_write(std::string_view sv) noexcept {
  uart_write(std::span<const uint8_t>{reinterpret_cast<const uint8_t*>(sv.data()), sv.size()});
}

// Compatibility overload for null-terminated C strings.
inline void uart_puts(const char* str) noexcept {
  uart_write(std::string_view{str});
}

} // namespace nova::board::qemu_virt
