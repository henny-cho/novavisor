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

// Power on a sibling vCPU (target_mpidr Aff0 = vCPU index). The target
// enters at `entry` with x0 = context_id and SP undefined — pass the
// stack top as context_id and let the entry stub install it
// (common/secondary.S). A concurrent duplicate reports ON_PENDING;
// once active it reports ALREADY_ON.
static inline int64_t psci_cpu_on(uint64_t target_mpidr, uint64_t entry, uint64_t context_id) {
  register uint64_t x0 __asm__("x0") = PSCI_FN_CPU_ON;
  register uint64_t x1 __asm__("x1") = target_mpidr;
  register uint64_t x2 __asm__("x2") = entry;
  register uint64_t x3 __asm__("x3") = context_id;
  __asm__ volatile("hvc #0" : "+r"(x0) : "r"(x1), "r"(x2), "r"(x3) : "memory");
  return (int64_t)x0;
}

// Retire the calling vCPU only — its siblings keep running. Does not
// return.
static inline void psci_cpu_off(void) {
  register uint64_t x0 __asm__("x0") = PSCI_FN_CPU_OFF;
  __asm__ volatile("hvc #0" : "+r"(x0)::"memory");
  __builtin_unreachable();
}

// Power state of a sibling vCPU: ON / OFF / ON_PENDING.
static inline int64_t psci_affinity_info(uint64_t target_mpidr) {
  register uint64_t x0 __asm__("x0") = PSCI_FN_AFFINITY_INFO;
  register uint64_t x1 __asm__("x1") = target_mpidr;
  __asm__ volatile("hvc #0" : "+r"(x0) : "r"(x1) : "memory");
  return (int64_t)x0;
}

#endif // NOVAVISOR_GUEST_PSCI_H
