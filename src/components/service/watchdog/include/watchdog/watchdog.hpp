#pragma once

// components/watchdog/include/watchdog/watchdog.hpp
//
// Per-VM heartbeat watchdog.
//
// A guest opts in with HVC_HEARTBEAT (x1 = window in ms): each call
// re-arms its VM's deadline slot to now + window, so a healthy guest
// never sees an expiry. Missing the window means the guest hung — the
// expiry callback warm-resets the VM (smp::reset_vm, affinity-routed),
// which also disarms the slot; the rebooted guest re-opts in with its
// next heartbeat. Calls from any sibling are routed to the boot vCPU's
// affinity core, which owns the VM's single deadline slot. x1 = 0
// disarms explicitly; boot generations reject delayed old-instance
// updates.

#include "trap_handler/hvc.hpp"

#include <cib/top.hpp>

namespace nova {

struct watchdog_component {
  // Claims HVC_HEARTBEAT.
  static void handle_hvc(HvcCall* call) noexcept;

  constexpr static auto config = cib::config(cib::extend<HvcService>(&watchdog_component::handle_hvc));
};

} // namespace nova
