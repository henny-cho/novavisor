#pragma once

// NovaVisor QEMU virt AArch64 Project Composition
//
// This file is the single assembly point for the QEMU virt ARM64 target.
// It declares which components are active for this target by listing them
// in cib::components<...> inside the project config struct.
//
// cib::top<nova_project> wires them all together at compile time
// (BSS is already cleared by hal/arch/aarch64/boot.S before any C++
// runs; EarlyRuntimeInit currently has no actions):
//   - RuntimeStart      ← core_mmu_component  (Stage 2 MMU activate)
//                         core_gic_component  (GICv3 + vIRQ interface)
//                         vgic_component      (GICD/GICR emulation, LRs)
//                         core_timer_component (CNTVOFF/CNTHP setup)
//                         soft_timer_component (CNTHP deadline slots)
//                         boot_msg_component  (UART boot banner)
//   - MainLoop          ← core_vcpu_component (ERET to EL1, [[noreturn]])
//
// components/idle/ is not composed (nor built): Phase 5 replaced the
// WFI idle loop with EL1 guest execution. Phase 11 may reintroduce it
// as a watchdog fallback.

#include "boot_msg/boot_msg.hpp"
#include "core_gic/core_gic.hpp"
#include "core_mmu/core_mmu.hpp"
#include "core_timer/core_timer.hpp"
#include "core_vcpu/core_vcpu.hpp"
#include "demo_hvc/demo_hvc.hpp"
#include "ivc/ivc.hpp"
#include "soft_timer/soft_timer.hpp"
#include "trap_handler/trap_handler.hpp"
#include "vgic/vgic.hpp"

#include <cib/top.hpp>

namespace nova {

struct nova_project {
  constexpr static auto config =
      cib::components<core_mmu_component, core_gic_component, vgic_component, core_timer_component,
                      soft_timer_component, boot_msg_component, trap_handler_component, demo_hvc_component,
                      ivc_component, core_vcpu_component>;
};

// nova_top is the concrete cib::top instantiation for this target.
// Calling nova_top{}.main() hands control to the CIB framework.
using nova_top = cib::top<nova_project>;

} // namespace nova
