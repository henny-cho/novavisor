#pragma once

#include "board.hpp"

#include <cstdint>
#include <span>
#include <string_view>

namespace novavisor::board::qemu_virt {

// Primary write primitive: all other overloads delegate here.
// Writes a span of bytes to PL011 UART0 via polling.
inline void uart_write(std::span<const uint8_t> data) noexcept {
  auto* const dr = reinterpret_cast<volatile uint32_t*>(UART0_BASE);
  for (const auto byte : data) {
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

} // namespace novavisor::board::qemu_virt
