// NovaVisor guest hypercall conduit (SMCCC v1.1, Arm DEN0028). Every
// demo hypercall goes through smccc_call: ID in x0, args in x1..x3,
// results in x0..x3, x4..x17 preserved. An unserved call still writes
// x1..x3 (NOT_SUPPORTED zeroes them), so all four are declared.

#ifndef NOVAVISOR_GUEST_SMCCC_H
#define NOVAVISOR_GUEST_SMCCC_H

#include "nova/abi/smccc.h"

#include <stdint.h>

static inline int64_t smccc_call(uint32_t fid, uint64_t a1, uint64_t a2, uint64_t a3) {
  register uint64_t x0 __asm__("x0") = fid;
  register uint64_t x1 __asm__("x1") = a1;
  register uint64_t x2 __asm__("x2") = a2;
  register uint64_t x3 __asm__("x3") = a3;
  __asm__ volatile("hvc #0" : "+r"(x0), "+r"(x1), "+r"(x2), "+r"(x3)::"memory");
  return (int64_t)x0;
}

// A call that should not return came back — nothing served it. Park the
// vCPU here instead of running on into whatever the compiler put next.
static inline __attribute__((noreturn)) void smccc_park(void) {
  for (;;) {
    __asm__ volatile("wfi");
  }
}

#endif // NOVAVISOR_GUEST_SMCCC_H
