// components/trap_handler/src/trap_handler.cpp
//
// Implements:
//   - C extern "C" entry points called from vec.S
//   - trap_handler_component::handle_lower_sync (default HVC stub)
//   - dump_trap_context register dump

#include "components/trap_handler/include/trap_handler.hpp"

#include "components/nova_panic/include/nova_panic.hpp"
#include "hal/console.hpp"
#include "nova/esr.hpp"
#include "nova/trap_context.hpp"

#include <cib/top.hpp>
#include <cstdint>

namespace nova {

// ---------------------------------------------------------------------------
// TrapContext register dump — shared by every fatal trap path
// ---------------------------------------------------------------------------

void dump_trap_context(const TrapContext* ctx) noexcept {
  using console::write;
  using console::write_dec64;
  using console::write_hex64;

  write("--- EL2 TRAP DUMP ---\n");

  write("ESR_EL2 : 0x");
  write_hex64(ctx->esr);
  write("  FAR_EL2 : 0x");
  write_hex64(ctx->far);
  write("\n");

  write("ELR_EL2 : 0x");
  write_hex64(ctx->elr);
  write("  SPSR    : 0x");
  write_hex64(ctx->spsr);
  write("\n");

  write("SP_EL1  : 0x");
  write_hex64(ctx->sp);
  write("\n");

  for (std::size_t i = 0; i < ctx->x.size(); ++i) {
    write("x");
    write_dec64(i);
    write(" : 0x");
    write_hex64(ctx->x[i]);
    write("\n");
  }

  write("---------------------\n");
}

// ---------------------------------------------------------------------------
// trap_handler_component::handle_lower_sync
//
// Default handler for lower-EL synchronous exceptions.
//   - HVC_AA64: dispatch to HvcService with the SMCCC function ID.
//   - All others: dump and halt.
// ---------------------------------------------------------------------------

void trap_handler_component::handle_lower_sync(TrapContext* ctx) noexcept {
  const auto ec = esr::get_ec(ctx->esr);

  if (ec == esr::ExceptionClass::HVC_AA64) {
    // SMCCC: the function ID lives in x0; the `hvc #imm16` instruction's
    // own immediate (ESR_EL2.ISS) is conventionally 0 and is NOT the
    // function selector. Pass the low 16 bits of x0 to the service.
    //
    // ELR_EL2 already points to the instruction AFTER the HVC per
    // ARM ARM §D1.11 — do NOT advance it here or the guest will skip
    // the next instruction on return. Handlers that halt (HVC_EXIT)
    // never return through this path anyway.
    const auto func_id = static_cast<std::uint16_t>(ctx->x[0] & 0xFFFFU);
    cib::service<HvcService>(ctx, func_id);
    return;
  }

  // Unexpected synchronous exception from lower EL
  console::write("[NOVA PANIC] Unexpected lower-EL sync exception\n");
  dump_trap_context(ctx);
  halt();
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
