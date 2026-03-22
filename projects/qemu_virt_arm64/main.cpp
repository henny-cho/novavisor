// NovaVisor bare-metal entry point for QEMU virt AArch64.
//
// Boot sequence (orchestrated by cib::top<nova_project>):
//   1. EarlyRuntimeInit → hal_init_component: clears BSS
//   2. RuntimeStart     → boot_msg_component: prints boot banner via UART
//   3. MainLoop (∞)    → idle_component: WFI idle loop

#include "nexus.hpp"

// nova_panic.cpp must be compiled as a translation unit to avoid ODR
// violations on the stdx::panic_handler<> specialization.
// The include here is only for documentation; linking is handled by CMake.

extern "C" void novavisor_main();

void novavisor_main() {
  nova::nova_top top{};
  top.main(); // [[noreturn]]
}
