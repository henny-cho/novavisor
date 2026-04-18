#pragma once

// components/trap_handler/include/trap_handler.hpp
//
// EL2SyncTrapService: CIB callback service dispatched on every EL2 synchronous
// exception (lower-EL or current-EL).  Components register handlers at compile
// time via cib::extend<EL2SyncTrapService>.
//
// Default stub handler (registered by trap_handler_component itself):
//   - HVC_AA64: advances ELR_EL2 by 4 and returns to guest.
//   - All others: dumps TrapContext to UART and halts.

#include "nova/trap_context.hpp"

#include <cib/top.hpp>
#include <nexus/callback.hpp>

namespace nova {

// ---------------------------------------------------------------------------
// EL2SyncTrapService
//
// Signature: void(TrapContext*)
// All registered callbacks are invoked in registration order on each trap.
// For HVC dispatch, a single component should own the routing.
// ---------------------------------------------------------------------------
struct EL2SyncTrapService : public callback::service<TrapContext*> {};

// ---------------------------------------------------------------------------
// HvcService
//
// Signature: void(TrapContext*, std::uint16_t func_id)
// Fired from handle_lower_sync whenever ESR_EL2.EC == HVC_AA64. The
// function ID is read from ctx->x[0] (SMCCC convention — the `hvc #imm16`
// instruction's own immediate is always 0). Each subscribed component
// should inspect `func_id` and act only on its own range (see
// demo/README.md for the allocation table):
//    0x1000..0x10FF  demo_hvc (PUTS/PUTC/EXIT/...)
//    0x1100..0x11FF  ivc      (Phase 7+)
//    0x1200..0x12FF  timer    (Phase 6+)
//
// Since every subscriber is called for every HVC, handlers must
// silently return when `imm` is outside their range — no "unknown"
// warnings.
// ---------------------------------------------------------------------------
struct HvcService : public callback::service<TrapContext*, std::uint16_t> {};

// ---------------------------------------------------------------------------
// trap_handler_component
// ---------------------------------------------------------------------------
struct trap_handler_component {
  // Default EL2 synchronous trap handler (lower EL).
  // Registered at compile time; invoked by el2_trap_lower_sync().
  static void handle_lower_sync(TrapContext* ctx) noexcept;

  // Exports both services and registers the default sync trap handler.
  // HvcService has no default subscriber; components (demo_hvc, ivc, ...)
  // extend it as needed.
  constexpr static auto config =
      cib::config(cib::exports<EL2SyncTrapService, HvcService>,
                  cib::extend<EL2SyncTrapService>(&trap_handler_component::handle_lower_sync));
};

} // namespace nova
