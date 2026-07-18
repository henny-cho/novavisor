#pragma once

// components/trap_handler/include/trap_handler/trap_handler.hpp
//
// EL2SyncTrapService: CIB callback service dispatched on every EL2
// synchronous exception from a lower EL.  Components register handlers
// at compile time via cib::extend<EL2SyncTrapService>.
//
// Default handler (registered by trap_handler_component itself) routes
// by ESR_EL2.EC:
//   - HVC_AA64         : dispatch HvcService (include/hvc.hpp)
//   - WFx              : dispatch WfxService (include/wfx.hpp)
//   - DATA_ABORT_LOWER : dispatch MmioService (include/mmio.hpp),
//                        escalating to GuestFaultService
//                        (include/guest_fault.hpp)
//   - all others       : dump TrapContext to UART and halt.
//
// Subscribers include only the service header they extend; this header
// is for the component itself (nexus composition) and the dump helper.

#include "nova/arch/trap_context.hpp"
#include "trap_handler/guest_fault.hpp"
#include "trap_handler/hvc.hpp"
#include "trap_handler/mmio.hpp"
#include "trap_handler/wfx.hpp"

#include <cib/top.hpp>
#include <nexus/callback.hpp>

namespace nova {

// ---------------------------------------------------------------------------
// EL2SyncTrapService
//
// Signature: void(TrapContext*)
// All registered callbacks are invoked in registration order on each trap.
// ---------------------------------------------------------------------------
struct EL2SyncTrapService : public callback::service<TrapContext*> {};

// Dump every TrapContext register to the console. Shared by all fatal
// trap paths (lower-EL default handler, EL2 self-trap, unhandled vector).
void dump_trap_context(const TrapContext* ctx) noexcept;

struct trap_handler_component {
  // Default EL2 synchronous trap handler (lower EL): EC-class router.
  // Registered at compile time; invoked by el2_trap_lower_sync().
  static void handle_lower_sync(TrapContext* ctx) noexcept;

  // Exports the trap services and registers the default sync trap
  // handler. HvcService / MmioService / GuestFaultService have no
  // default subscriber; components (demo_hvc, vgic, core_vcpu, ...)
  // extend them as needed.
  constexpr static auto config =
      cib::config(cib::exports<EL2SyncTrapService, HvcService, WfxService, MmioService, GuestFaultService>,
                  cib::extend<EL2SyncTrapService>(&trap_handler_component::handle_lower_sync));
};

} // namespace nova
