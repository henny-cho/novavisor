// NovaVisor demo HVC helpers.
//
// Inline stubs for the NovaVisor guest hypercall ABI. Shared by every
// demo guest. The ABI is documented in demo/README.md and is the
// contract between guests and the hypervisor's HVC dispatcher.
//
// Function ID lives in x0. Arguments in x1..x6. Return (if any) in x0.

#ifndef NOVAVISOR_DEMO_HVC_H
#define NOVAVISOR_DEMO_HVC_H

#include <stddef.h>
#include <stdint.h>

enum {
  HVC_PUTS      = 0x1000,
  HVC_PUTC      = 0x1001,
  HVC_EXIT      = 0x1002,
  HVC_YIELD     = 0x1003,
  HVC_HEARTBEAT = 0x1004,
  // IVC range (Phase 7)
  HVC_IVC_DOORBELL = 0x1100,
  // Timer range (Phase 6)
  HVC_TIMER_SET = 0x1200,
};

static inline void hvc_putc(char c) {
  register uint64_t x0 __asm__("x0") = HVC_PUTC;
  register uint64_t x1 __asm__("x1") = (uint64_t)(unsigned char)c;
  __asm__ volatile("hvc #0" : "+r"(x0) : "r"(x1) : "memory");
}

static inline void hvc_puts(const char* s, size_t n) {
  register uint64_t x0 __asm__("x0") = HVC_PUTS;
  register uint64_t x1 __asm__("x1") = (uint64_t)(uintptr_t)s;
  register uint64_t x2 __asm__("x2") = (uint64_t)n;
  __asm__ volatile("hvc #0" : "+r"(x0) : "r"(x1), "r"(x2) : "memory");
}

// Convenience: print a C string literal whose length the compiler knows.
#define hvc_puts_lit(s) hvc_puts((s), sizeof(s) - 1)

static inline void hvc_exit(int code) {
  register uint64_t x0 __asm__("x0") = HVC_EXIT;
  register uint64_t x1 __asm__("x1") = (uint64_t)code;
  __asm__ volatile("hvc #0" : "+r"(x0) : "r"(x1) : "memory");
  __builtin_unreachable();
}

static inline void hvc_yield(void) {
  register uint64_t x0 __asm__("x0") = HVC_YIELD;
  __asm__ volatile("hvc #0" : "+r"(x0)::"memory");
}

static inline void hvc_heartbeat(uint64_t vm_id) {
  register uint64_t x0 __asm__("x0") = HVC_HEARTBEAT;
  register uint64_t x1 __asm__("x1") = vm_id;
  __asm__ volatile("hvc #0" : "+r"(x0) : "r"(x1) : "memory");
}

#endif // NOVAVISOR_DEMO_HVC_H
