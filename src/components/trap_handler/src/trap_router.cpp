// components/trap_handler/src/trap_router.cpp
//
// Implements:
//   - C extern "C" entry points called from vec.S
//   - trap_handler_component::handle_lower_sync (EC-class router)
//
// Per-class dispatch lives in its own TU (data_abort.cpp); the register
// dump in trap_dump.cpp.

#include "components/nova_panic/include/nova_panic.hpp"
#include "components/trap_handler/include/trap_handler.hpp"
#include "components/trap_handler/src/dispatch.hpp"
#include "hal/console.hpp"
#include "nova/arch/esr.hpp"
#include "nova/arch/trap_context.hpp"

#include <cib/top.hpp>
#include <cstdint>

namespace nova {

namespace {

void dispatch_hvc(TrapContext* ctx) noexcept {
  // SMCCC: the function ID lives in x0; the `hvc #imm16` instruction's
  // own immediate (ESR_EL2.ISS) is conventionally 0 and is NOT the
  // function selector. Pass the low 16 bits of x0 to the service.
  //
  // ELR_EL2 already points to the instruction AFTER the HVC per
  // ARM ARM §D1.11 — do NOT advance it here or the guest will skip
  // the next instruction on return. Handlers that halt (HVC_EXIT)
  // never return through this path anyway.
  HvcCall call{.ctx = ctx, .func_id = static_cast<std::uint16_t>(ctx->x[0] & esr::kHvcImmMask), .handled = false};
  cib::service<HvcService>(&call);

  if (!call.handled) {
    console::write("[trap_handler] unknown HVC func_id=0x");
    console::write_hex64(call.func_id);
    console::write(" — returning SMCCC NOT_SUPPORTED\n");
    ctx->x[0] = kSmcccNotSupported;
  }
}

} // namespace

// EC-class router for lower-EL synchronous exceptions. Each supported
// class gets a case that forwards to its dispatch; everything else
// dumps and halts.
void trap_handler_component::handle_lower_sync(TrapContext* ctx) noexcept {
  switch (esr::get_ec(ctx->esr)) {
  case esr::ExceptionClass::HVC_AA64:
    dispatch_hvc(ctx);
    return;

  case esr::ExceptionClass::DATA_ABORT_LOWER:
    trap::dispatch_data_abort(ctx);
    return;

  default:
    console::write("[NOVA PANIC] Unexpected lower-EL sync exception\n");
    dump_trap_context(ctx);
    halt();
  }
}

} // namespace nova

// ---------------------------------------------------------------------------
// extern "C" entry points — called directly from vec.S
// ---------------------------------------------------------------------------

extern "C" {

void el2_trap_lower_sync(nova::TrapContext* ctx) noexcept {
  cib::service<nova::EL2SyncTrapService>(ctx);
}

void el2_trap_current_sync(nova::TrapContext* ctx) noexcept {
  // Dump directly — the CIB service is not dispatched on this path
  // because the exception occurred inside EL2 itself.
  nova::console::write("[NOVA PANIC] EL2 self-trap (current-EL sync)\n");
  nova::dump_trap_context(ctx);
  nova::halt();
}

void el2_trap_unhandled(nova::TrapContext* ctx) noexcept {
  nova::console::write("[NOVA PANIC] Unhandled EL2 exception\n");
  nova::dump_trap_context(ctx);
  nova::halt();
}

} // extern "C"
