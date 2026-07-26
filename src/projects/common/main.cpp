// NovaVisor bare-metal entry point, shared by every project profile.
// The composition it boots is whichever nexus.hpp the project puts on
// the include path (BSS is cleared in hal/arch/aarch64/boot/boot.S; the rest
// is orchestrated by cib::top<nova_project>):
//   1. RuntimeStart → the project's init chain (Stage 2, GIC, timers,
//                     …), ending with the boot banner.
//   2. MainLoop     → core_vcpu_component: ERET into EL1 ([[noreturn]]).

#include "guest_config.hpp"
#include "nexus.hpp"

#include <cstdint>

extern "C" void novavisor_main();

void novavisor_main() {
  // Boot core only (secondaries enter via novavisor_secondary), before
  // RuntimeStart — every guest_table() consumer sees a populated table.
  nova::project::init_guest_table();
  nova::nova_top top{};
  top.main(); // [[noreturn]]
}

// Secondary-core fallback for profiles that do not compose smp: park the
// core forever. Weak so smp's real bring-up entry overrides it whenever
// that component is linked in.
extern "C" [[noreturn]] __attribute__((weak)) void novavisor_secondary(std::uint64_t) noexcept {
  for (;;) {
    asm volatile("wfi");
  }
}
