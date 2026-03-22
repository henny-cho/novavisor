#pragma once

// HAL Initialization Component
//
// Extends cib::EarlyRuntimeInit — runs as the very first action in the boot
// sequence before any other component initializes.
//
// Responsibilities:
//   1. Clear the .bss section (zero-initialize all uninitialized globals)
//
// NOTE: uart.hpp uses UART0_BASE which is a compile-time constant from
// board.hpp, so UART output in later phases is immediately available
// after this component runs.

#include "hal/board_qemu_virt/include/board.hpp"

#include <cib/top.hpp>
#include <cstdint>
#include <flow/flow.hpp>

extern "C" {
extern uint64_t bss_start;
extern uint64_t bss_end;
}

namespace nova {

struct hal_init_component {
  constexpr static auto INIT = flow::action<"hal_init">([]() noexcept {
    // Zero-initialize the BSS section via volatile pointers.
    // Volatile prevents GCC from applying object-size analysis on the
    // linker-marker symbols bss_start/bss_end (which are address labels,
    // not actual uint64_t objects).
    auto*                         p   = reinterpret_cast<volatile uint8_t*>(&bss_start);
    const volatile uint8_t* const end = reinterpret_cast<volatile uint8_t*>(&bss_end);
    while (p < end) {
      *p++ = 0U;
    }
  });

  constexpr static auto config = cib::config(cib::extend<cib::EarlyRuntimeInit>(*INIT));
};

} // namespace nova
