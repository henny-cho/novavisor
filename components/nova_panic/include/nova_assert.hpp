#pragma once

// NOVA_ASSERT / NOVA_CHECK / NOVA_CRASH
//
// Bare-metal assertion helpers. All paths terminate via stdx::panic<ct_string>()
// which is specialized in components/nova_panic/src/nova_panic.cpp and converges
// on uart_write + WFI halt (see nova::NovaPanicHandler).
//
// Why macros (not functions): the panic message is a ct_string template
// argument, so the literal must be visible at the call site.

#include <stdx/panic.hpp>

#define NOVA_CRASH(msg) STDX_PANIC(msg)

#define NOVA_ASSERT(expr)                                                                                              \
  do {                                                                                                                 \
    if (!(expr)) {                                                                                                     \
      STDX_PANIC("NOVA_ASSERT failed");                                                                                \
    }                                                                                                                  \
  } while (0)

#define NOVA_CHECK(expr) NOVA_ASSERT(expr)
