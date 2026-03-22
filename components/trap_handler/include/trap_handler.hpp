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
// trap_handler_component
// ---------------------------------------------------------------------------
struct trap_handler_component {
  // Default EL2 synchronous trap handler (lower EL).
  // Registered at compile time; invoked by el2_trap_lower_sync().
  static void handle_lower_sync(TrapContext* ctx) noexcept;

  // Exports the service so other components can extend it.
  // Provides a default handler that handles HVC stubs and panics on anything
  // unexpected.
  constexpr static auto config = cib::config(
      cib::exports<EL2SyncTrapService>, cib::extend<EL2SyncTrapService>(&trap_handler_component::handle_lower_sync));
};

} // namespace nova
