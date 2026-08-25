// NovaVisor guest SMCCC Arch service helpers.
//
// Inline stubs for the non-PSCI half of the firmware interface a guest
// expects (Arm DEN0028): the calling-convention version, feature
// discovery, and the CPU speculation workarounds. Function IDs come
// from nova/abi/smccc.h — the single source shared with the
// hypervisor's implementation (the psci component's smccc_model).
//
// Function ID lives in x0. Arguments in x1..x3. Return in x0.

#ifndef NOVAVISOR_GUEST_SMCCC_H
#define NOVAVISOR_GUEST_SMCCC_H

#include "nova/abi/smccc.h"

#include <stdint.h>

// One SMCCC Arch call. `arg` is x1 — the queried function ID for
// ARCH_FEATURES, ignored by the rest.
static inline int64_t smccc_call(uint32_t fid, uint64_t arg) {
  register uint64_t x0 __asm__("x0") = fid;
  register uint64_t x1 __asm__("x1") = arg;
  __asm__ volatile("hvc #0" : "+r"(x0) : "r"(x1) : "memory");
  return (int64_t)x0;
}

#endif // NOVAVISOR_GUEST_SMCCC_H
