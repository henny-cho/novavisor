// NovaVisor guest PSCI helpers.
//
// Inline stubs for the standard PSCI calls (Arm DEN0022) over the HVC
// conduit. Function IDs come from nova/abi/psci.h — the single source
// shared with the hypervisor's implementation (components/psci).
//
// Function ID lives in x0. Arguments in x1..x3. Return (if any) in x0.

#ifndef NOVAVISOR_GUEST_PSCI_H
#define NOVAVISOR_GUEST_PSCI_H

#include "nova/abi/psci.h"

#include <stdint.h>

static inline uint64_t psci_version(void) {
  register uint64_t x0 __asm__("x0") = PSCI_FN_VERSION;
  __asm__ volatile("hvc #0" : "+r"(x0)::"memory");
  return x0;
}

// Power off the calling VM. Does not return.
static inline void psci_system_off(void) {
  register uint64_t x0 __asm__("x0") = PSCI_FN_SYSTEM_OFF;
  __asm__ volatile("hvc #0" : "+r"(x0)::"memory");
  __builtin_unreachable();
}

// Warm-reboot the calling VM from its pristine image. Does not return
// to the call site — execution resumes at the guest entry point.
static inline void psci_system_reset(void) {
  register uint64_t x0 __asm__("x0") = PSCI_FN_SYSTEM_RESET;
  __asm__ volatile("hvc #0" : "+r"(x0)::"memory");
  __builtin_unreachable();
}

#endif // NOVAVISOR_GUEST_PSCI_H
