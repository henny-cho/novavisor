#pragma once

// components/trap_handler/include/trap_handler.hpp
//
// EL2SyncTrapService: CIB callback service dispatched on every EL2
// synchronous exception from a lower EL.  Components register handlers
// at compile time via cib::extend<EL2SyncTrapService>.
//
// Default handler (registered by trap_handler_component itself) routes
// by ESR_EL2.EC:
//   - HVC_AA64   : dispatch HvcService; warn + SMCCC NOT_SUPPORTED if
//                  no subscriber claims the call.
//   - all others : dump TrapContext to UART and halt.
// Phase 6 (WFx) and Phase 8 (DATA_ABORT_LOWER) add cases to the same
// switch and route to their own services.

#include "nova/trap_context.hpp"

#include <cib/top.hpp>
#include <cstdint>
#include <nexus/callback.hpp>

namespace nova {

// ---------------------------------------------------------------------------
// EL2SyncTrapService
//
// Signature: void(TrapContext*)
// All registered callbacks are invoked in registration order on each trap.
// ---------------------------------------------------------------------------
struct EL2SyncTrapService : public callback::service<TrapContext*> {};

// ---------------------------------------------------------------------------
// HvcService
//
// One guest HVC, dispatched to every subscriber. The function ID is
// read from ctx->x[0] (SMCCC convention — the `hvc #imm16` instruction's
// own immediate is always 0); the allocation table lives in
// nova/hvc_abi.h (shared with the guests).
//
// Each subscriber inspects `func_id`, acts only on IDs it implements,
// and sets `handled = true` for those — silently returning otherwise.
// If no subscriber claims the call, trap_handler warns and returns
// SMCCC NOT_SUPPORTED (-1) in the guest's x0.
// ---------------------------------------------------------------------------
// SMCCC v1.x: unimplemented functions and invalid arguments return -1
// in x0. Shared by the dispatcher and every HvcService subscriber.
inline constexpr std::uint64_t kSmcccNotSupported = ~0ULL;

struct HvcCall {
  TrapContext*  ctx     = nullptr;
  std::uint16_t func_id = 0;
  bool          handled = false;
};

struct HvcService : public callback::service<HvcCall*> {};

// ---------------------------------------------------------------------------
// trap_handler_component
// ---------------------------------------------------------------------------
// Dump every TrapContext register to the console. Shared by all fatal
// trap paths (lower-EL default handler, EL2 self-trap, unhandled vector).
void dump_trap_context(const TrapContext* ctx) noexcept;

struct trap_handler_component {
  // Default EL2 synchronous trap handler (lower EL): EC-class router.
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
