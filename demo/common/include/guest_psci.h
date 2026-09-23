// NovaVisor guest PSCI helpers.
//
// Inline stubs for the standard PSCI calls (Arm DEN0022) over the HVC
// conduit. Function IDs come from nova/abi/psci.h — the single source
// shared with the hypervisor's implementation (the psci component).
//
// The register contract is the conduit's, in guest_smccc.h.

#ifndef NOVAVISOR_GUEST_PSCI_H
#define NOVAVISOR_GUEST_PSCI_H

#include "guest_smccc.h"
#include "nova/abi/psci.h"

#include <stdint.h>

static inline uint64_t psci_version(void) {
  return (uint64_t)smccc_call(PSCI_FN_VERSION, 0, 0, 0);
}

// Power off the calling VM. Does not return.
static inline void psci_system_off(void) {
  (void)smccc_call(PSCI_FN_SYSTEM_OFF, 0, 0, 0);
  smccc_park();
}

// Warm-reboot the calling VM from its pristine image. Does not return
// to the call site — execution resumes at the guest entry point.
static inline void psci_system_reset(void) {
  (void)smccc_call(PSCI_FN_SYSTEM_RESET, 0, 0, 0);
  smccc_park();
}

// Power on a sibling vCPU (target_mpidr Aff0 = vCPU index). The target
// enters at `entry` with x0 = context_id and SP undefined — pass the
// stack top as context_id and let the entry stub install it
// (common/secondary.S). A concurrent duplicate reports ON_PENDING;
// once active it reports ALREADY_ON.
static inline int64_t psci_cpu_on(uint64_t target_mpidr, uint64_t entry, uint64_t context_id) {
  return smccc_call(PSCI_FN_CPU_ON, target_mpidr, entry, context_id);
}

// Retire the calling vCPU only — its siblings keep running. Does not
// return.
static inline void psci_cpu_off(void) {
  (void)smccc_call(PSCI_FN_CPU_OFF, 0, 0, 0);
  smccc_park();
}

// Whether the firmware implements one function ID — PSCI's range and
// the SMCCC Arch range both. Guest Linux gates all of SMCCC 1.1 on it.
static inline int64_t psci_features(uint32_t queried) {
  return smccc_call(PSCI_FN_FEATURES, queried, 0, 0);
}

// Power state of a sibling vCPU: ON / OFF / ON_PENDING.
static inline int64_t psci_affinity_info(uint64_t target_mpidr) {
  return smccc_call(PSCI_FN_AFFINITY_INFO, target_mpidr, 0, 0);
}

#endif // NOVAVISOR_GUEST_PSCI_H
