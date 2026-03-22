// components/nova_panic/src/pw_assert_backend.cc
//
// pw_assert_HandleFailure() implementation — routes PW_ASSERT / PW_CHECK /
// PW_CRASH to the same UART + WFI halt path as CIB stdx::panic (nova_panic).
//
// Panic convergence:
//   CIB stdx::panic      ──┐
//   PW_ASSERT / PW_CHECK ──┤→ uart_write → halt()
//   PW_CRASH             ──┘
//
// Note: includes uart.hpp directly (not nova_panic.hpp) to avoid pulling in
// stdx/ct_string.hpp which is a CIB dependency not available in all targets.

#include "hal/board_qemu_virt/include/uart.hpp"

extern "C" {

[[noreturn]] void pw_assert_HandleFailure() {
  nova::board::qemu_virt::uart_write("[NOVA PANIC] PW_ASSERT/CHECK failed. System halted.\n");
  while (true) {
    asm volatile("wfi");
  }
}

} // extern "C"
