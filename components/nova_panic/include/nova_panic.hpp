#pragma once

#include "hal/board_qemu_virt/include/uart.hpp"

#include <stdx/ct_string.hpp>

namespace novavisor {

// Custom stdx panic handler for bare-metal.
// On any CIB assertion failure (e.g. calling an uninitialized service),
// prints a UART message then halts the CPU.
struct NovaPanicHandler {
  template <typename... Args>
  static auto panic(Args&&... /*args*/) noexcept -> void { // NOLINT(cppcoreguidelines-missing-std-forward)
    board::qemu_virt::uart_puts("[NOVA PANIC] System halted.\n");
    while (true) {
      asm volatile("wfi");
    }
  }

  template <stdx::ct_string S, typename... Args>
  static auto panic(Args&&... /*args*/) noexcept -> void { // NOLINT(cppcoreguidelines-missing-std-forward)
    board::qemu_virt::uart_puts("[NOVA PANIC] ");
    board::qemu_virt::uart_puts(S.data());
    board::qemu_virt::uart_puts("\n[NOVA PANIC] System halted.\n");
    while (true) {
      asm volatile("wfi");
    }
  }
};

} // namespace novavisor
