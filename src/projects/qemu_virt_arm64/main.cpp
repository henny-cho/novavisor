// NovaVisor bare-metal entry point for QEMU virt AArch64.
//
// Boot sequence (BSS cleared in hal/armv8_aarch64/boot.S; the rest
// orchestrated by cib::top<nova_project>):
//   1. RuntimeStart → core_mmu_component:   activates Stage 2 MMU
//                     core_gic_component:   GICv3 + vIRQ interface
//                     core_timer_component: CNTVOFF/CNTHP setup
//                     boot_msg_component:   prints boot banner via UART
//   2. MainLoop     → core_vcpu_component:  ERET into EL1 guest ([[noreturn]])

#include "nexus.hpp"

// nova_panic.cpp must be compiled as a translation unit to avoid ODR
// violations on the stdx::panic_handler<> specialization.
// The include here is only for documentation; linking is handled by CMake.

extern "C" void novavisor_main();

void novavisor_main() {
  nova::nova_top top{};
  top.main(); // [[noreturn]]
}
