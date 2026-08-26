// NovaVisor guest SMCCC Arch service helpers (Arm DEN0028): version,
// feature discovery, speculation workarounds. IDs from nova/abi/smccc.h,
// shared with the hypervisor's smccc_model. ID in x0, args x1..x3,
// return in x0.

#ifndef NOVAVISOR_GUEST_SMCCC_H
#define NOVAVISOR_GUEST_SMCCC_H

#include "nova/abi/smccc.h"

#include <stdint.h>

// `arg` is x1: the queried function ID for ARCH_FEATURES, ignored otherwise.
static inline int64_t smccc_call(uint32_t fid, uint64_t arg) {
  register uint64_t x0 __asm__("x0") = fid;
  register uint64_t x1 __asm__("x1") = arg;
  __asm__ volatile("hvc #0" : "+r"(x0) : "r"(x1) : "memory");
  return (int64_t)x0;
}

#endif // NOVAVISOR_GUEST_SMCCC_H
