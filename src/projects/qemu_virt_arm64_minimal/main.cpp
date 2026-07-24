#include "guest_config.hpp"
#include "nexus.hpp"

#include <cstdint>

extern "C" void novavisor_main();

void novavisor_main() {
  nova::qemu_virt::init_guest_table();
  nova::nova_top top{};
  top.main();
}

extern "C" [[noreturn]] void novavisor_secondary(std::uint64_t) noexcept {
  for (;;) {
    asm volatile("wfi");
  }
}
