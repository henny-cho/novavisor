#pragma once

// components/demo_hvc/include/demo_hvc.hpp
//
// Hypervisor-side implementation of the demo guest HVC ABI
// (see demo/README.md HVC allocation table). Phase 5 implements:
//
//   HVC_PUTS  (0x1000)  x1 = IPA of byte buffer, x2 = length
//   HVC_PUTC  (0x1001)  x1 = character (low byte)
//   HVC_EXIT  (0x1002)  x1 = exit code; prints "demo_exit code=<n>"
//                               and halts the hypervisor.
//
// Extends HvcService. Called on every HVC — silently returns for imms
// outside the demo range (0x1000..0x10FF).

#include "components/trap_handler/include/trap_handler.hpp"

#include <cib/top.hpp>
#include <cstdint>

namespace nova {

struct demo_hvc_component {
  static void handle_hvc(TrapContext* ctx, std::uint16_t imm) noexcept;

  constexpr static auto config = cib::config(cib::extend<HvcService>(&demo_hvc_component::handle_hvc));
};

} // namespace nova
