#pragma once

// components/ivc/include/ivc.hpp
//
// Inter-VM communication doorbell.
//
// The hypervisor's IVC role is deliberately minimal: core_mmu maps one
// shared page RW into every VM at boot, and this component turns
// HVC_IVC_DOORBELL into a doorbell vIRQ (SGI 0) on the target VCPU via
// vcpu::post_virq. The message protocol inside the shared page is
// entirely guest-owned (demo/common/include/ivc_shm.h).

#include "components/trap_handler/include/hvc.hpp"

#include <cib/top.hpp>

namespace nova {

struct ivc_component {
  // Claims HVC_IVC_DOORBELL (x1 = target guest_table index; returns 0
  // in x0, or SMCCC NOT_SUPPORTED when the target is invalid/off).
  static void handle_hvc(HvcCall* call) noexcept;

  constexpr static auto config = cib::config(cib::extend<HvcService>(&ivc_component::handle_hvc));
};

} // namespace nova
