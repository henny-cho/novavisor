#pragma once

#include "board.hpp"

#include <cstdint>
#include <span>

namespace novavisor::board::qemu_virt {

// Write a span of bytes to PL011 UART0 (polling).
// This is the primary UART output primitive used by all higher-level
// logging and console components.
inline void uart_write(std::span<const uint8_t> data) noexcept {
  auto* const dr = reinterpret_cast<volatile uint32_t*>(UART0_BASE); // NOLINT
  for (const auto byte : data) {
    *dr = static_cast<uint32_t>(byte);
  }
}

// Convenience wrapper: write a null-terminated C string to UART0.
inline void uart_puts(const char* str) noexcept {
  while (*str != '\0') {
    auto* const dr = reinterpret_cast<volatile uint32_t*>(UART0_BASE); // NOLINT
    *dr            = static_cast<uint32_t>(*str);
    ++str; // NOLINT
  }
}

} // namespace novavisor::board::qemu_virt
