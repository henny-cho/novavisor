#pragma once

#include "board.hpp"

#include <cstdint>
#include <span>
#include <string_view>

namespace nova::board::qemu_virt {

// PL011 register offsets and flags (ARM DDI0183, §3.2).
inline constexpr uintptr_t kUartFrOffset   = 0x18;    // Flag Register
inline constexpr uintptr_t kUartImscOffset = 0x38;    // Interrupt Mask Set/Clear
inline constexpr uint32_t  kUartFrTxFull   = 1U << 5; // FR.TXFF — TX FIFO full
inline constexpr uint32_t  kUartFrRxEmpty  = 1U << 4; // FR.RXFE — RX FIFO empty
inline constexpr uint32_t  kUartIntRx      = 1U << 4; // IMSC.RXIM — RX interrupt

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

// Non-blocking RX: one byte, or -1 when the FIFO is empty.
inline auto uart_try_read() noexcept -> int {
  const auto* const fr = reinterpret_cast<const volatile uint32_t*>(UART0_BASE + kUartFrOffset);
  if ((*fr & kUartFrRxEmpty) != 0U) {
    return -1;
  }
  return static_cast<int>(*reinterpret_cast<const volatile uint32_t*>(UART0_BASE) & 0xFFU);
}

// Unmask the RX interrupt (reset LCR_H has FIFOs disabled — character
// mode, one interrupt per received byte). The GIC-side routing of the
// UART SPI is the caller's job.
inline void uart_rx_irq_enable() noexcept {
  auto* const imsc = reinterpret_cast<volatile uint32_t*>(UART0_BASE + kUartImscOffset);
  *imsc            = *imsc | kUartIntRx;
}

} // namespace nova::board::qemu_virt
