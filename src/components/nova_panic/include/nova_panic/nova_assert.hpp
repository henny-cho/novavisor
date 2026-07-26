#pragma once

// NOVA_ASSERT / NOVA_CHECK / NOVA_CRASH
//
// Bare-metal assertion helpers. All paths terminate via stdx::panic<ct_string>()
// which is specialized in components/nova_panic/src/nova_panic.cpp and converges
// on console::write + WFI halt (see nova::NovaPanicHandler).
//
// Why macros (not functions): the panic message is a ct_string template
// argument, so the literal must be visible at the call site.

#include <stdx/panic.hpp>

#define NOVA_CRASH(msg) STDX_PANIC(msg)

// Location baked into the compile-time panic string — every assertion
// used to print the same "NOVA_ASSERT failed" line, which made a
// serial-log-only failure unattributable.
#define NOVA_STRINGIZE_DETAIL(x) #x
#define NOVA_STRINGIZE(x)        NOVA_STRINGIZE_DETAIL(x)

#define NOVA_ASSERT(expr)                                                                                              \
  do {                                                                                                                 \
    if (!(expr)) {                                                                                                     \
      STDX_PANIC("NOVA_ASSERT failed: " #expr " (" __FILE__ ":" NOVA_STRINGIZE(__LINE__) ")");                         \
    }                                                                                                                  \
  } while (0)

#define NOVA_CHECK(expr) NOVA_ASSERT(expr)
