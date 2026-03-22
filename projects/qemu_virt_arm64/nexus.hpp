#pragma once

// NovaVisor QEMU virt AArch64 Project Composition
//
// This file is the single assembly point for the QEMU virt ARM64 target.
// It declares which components are active for this target by listing them
// in cib::components<...> inside the project config struct.
//
// cib::top<nova_project> wires them all together at compile time:
//   - EarlyRuntimeInit  ← hal_init_component (BSS clear)
//   - RuntimeStart      ← boot_msg_component (UART boot banner)
//   - MainLoop          ← idle_component     (WFI idle)
//
// To add a new component in future phases (e.g., core_mmu, vgic),
// simply include its header and add it to cib::components<...>.

#include "components/boot_msg/include/boot_msg.hpp"
#include "components/hal_init/include/hal_init.hpp"
#include "components/idle/include/idle.hpp"
#include "components/trap_handler/include/trap_handler.hpp"

#include <cib/top.hpp>

namespace nova {

struct nova_project {
  constexpr static auto config =
      cib::components<hal_init_component, boot_msg_component, idle_component, trap_handler_component>;
};

// nova_top is the concrete cib::top instantiation for this target.
// Calling nova_top{}.main() hands control to the CIB framework.
using nova_top = cib::top<nova_project>;

} // namespace nova
