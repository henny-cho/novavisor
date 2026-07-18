#pragma once

// components/psci/include/psci/psci.hpp
//
// Guest-facing PSCI over the HVC conduit.
//
// Guests control their own power through the standard SMCCC range
// (0x8400_xxxx) instead of NOVA-private HVCs: SYSTEM_OFF stops the
// calling VM, SYSTEM_RESET warm-reboots it (pristine image reload,
// core_vcpu::reset_vm). The pure dispatch table lives in
// psci_model.hpp; this component binds its actions to the scheduler.

#include "trap_handler/hvc.hpp"

#include <cib/top.hpp>

namespace nova {

struct psci_component {
  // Claims the whole PSCI function range (SMC32 + SMC64 forms).
  static void handle_hvc(HvcCall* call) noexcept;

  constexpr static auto config = cib::config(cib::extend<HvcService>(&psci_component::handle_hvc));
};

} // namespace nova
