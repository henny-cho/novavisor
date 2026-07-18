#pragma once

// components/demo_hvc/include/demo_hvc/demo_hvc.hpp
//
// Hypervisor-side implementation of the demo guest HVC ABI
// (see demo/README.md HVC allocation table). Phase 5 implements:
//
//   HVC_PUTS  (0x1000)  x1 = IPA of byte buffer, x2 = length
//   HVC_PUTC  (0x1001)  x1 = character (low byte)
//   HVC_EXIT  (0x1002)  x1 = exit code; prints "demo_exit code=<n>"
//                               and halts the hypervisor.
//
// Extends HvcService. Called on every HVC — claims (sets handled on)
// the IDs above and silently returns for everything else, including
// unallocated IDs inside the demo range (trap_handler then reports
// them as unknown).

#include "trap_handler/hvc.hpp"

#include <cib/top.hpp>

namespace nova {

struct demo_hvc_component {
  static void handle_hvc(HvcCall* call) noexcept;

  constexpr static auto config = cib::config(cib::extend<HvcService>(&demo_hvc_component::handle_hvc));
};

} // namespace nova
