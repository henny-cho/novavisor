#include "../../hal/board_qemu_virt/include/board.hpp"

extern "C" {
extern uint64_t bss_start;
extern uint64_t bss_end;
void            novavisor_main();
}

static void clear_bss() {
  auto* b_start = reinterpret_cast<uint8_t*>(&bss_start); // NOLINT
  auto* b_end   = reinterpret_cast<uint8_t*>(&bss_end);   // NOLINT
  for (uint8_t* ptr = b_start; ptr < b_end; ++ptr) {      // NOLINT
    *ptr = 0;
  }
}

static void uart_print(const char* str) {
  auto* uart0_dr = reinterpret_cast<volatile uint32_t*>(novavisor::board::qemu_virt::UART0_BASE); // NOLINT
  while (*str != '\0') {
    *uart0_dr = static_cast<uint32_t>(*str);
    str++; // NOLINT
  }
}

void novavisor_main() {
  clear_bss();
  uart_print("NovaVisor Booted!\n");

  // Infinite loop
  while (true) {
    asm volatile("wfi");
  }
}
