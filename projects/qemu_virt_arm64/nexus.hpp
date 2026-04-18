#pragma once

// NovaVisor QEMU virt AArch64 Project Composition
//
// This file is the single assembly point for the QEMU virt ARM64 target.
// It declares which components are active for this target by listing them
// in cib::components<...> inside the project config struct.
//
// cib::top<nova_project> wires them all together at compile time:
//   - EarlyRuntimeInit  ← hal_init_component  (BSS clear)
//   - RuntimeStart      ← core_mmu_component  (Stage 2 MMU activate)
//                         boot_msg_component  (UART boot banner)
//   - MainLoop          ← core_vcpu_component (ERET to EL1, [[noreturn]])
//
// idle_component is built but not composed here: Phase 5 replaces the
// WFI idle loop with EL1 guest execution. Phase 11 may reintroduce it
// as a watchdog fallback.

#include "components/boot_msg/include/boot_msg.hpp"
#include "components/core_mmu/include/core_mmu.hpp"
#include "components/core_vcpu/include/core_vcpu.hpp"
#include "components/demo_hvc/include/demo_hvc.hpp"
#include "components/hal_init/include/hal_init.hpp"
#include "components/trap_handler/include/trap_handler.hpp"

#include <cib/top.hpp>

namespace nova {

struct nova_project {
  constexpr static auto config = cib::components<hal_init_component, core_mmu_component, boot_msg_component,
                                                 trap_handler_component, demo_hvc_component, core_vcpu_component>;
};

// nova_top is the concrete cib::top instantiation for this target.
// Calling nova_top{}.main() hands control to the CIB framework.
using nova_top = cib::top<nova_project>;

} // namespace nova
