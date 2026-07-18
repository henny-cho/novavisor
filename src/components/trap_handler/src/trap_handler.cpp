// components/trap_handler/src/trap_handler.cpp
//
// Implements:
//   - C extern "C" entry points called from vec.S
//   - trap_handler_component::handle_lower_sync (EC-class router)
//   - dump_trap_context register dump

#include "components/trap_handler/include/trap_handler.hpp"

#include "components/nova_panic/include/nova_panic.hpp"
#include "hal/console.hpp"
#include "nova/arch/esr.hpp"
#include "nova/arch/trap_context.hpp"

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
// EC-class router for lower-EL synchronous exceptions. Each supported
// class gets a case that forwards to its service; everything else dumps
// and halts. Phase 6 (WFx) and Phase 8 (DATA_ABORT_LOWER) extend the
// switch with their own cases.
// ---------------------------------------------------------------------------

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

// Escalate an unrecoverable guest fault. When a subscriber claims it,
// it has retired the faulting VCPU and swapped the live frame to the
// next runnable one — returning resumes that guest. Unclaimed means
// nobody owns VM lifecycles: stop the machine.
void dispatch_guest_fault(TrapContext* ctx) noexcept {
  GuestFaultCall call{.ctx = ctx, .handled = false};
  cib::service<GuestFaultService>(&call);
  if (!call.handled) {
    halt();
  }
}

// Stage 2 Data Abort from the guest: decode the syndrome and emulate
// the access through MmioService. Anything without a full syndrome
// (ISV=0: load/store pair, writeback, ...) or that is not a plain
// translation fault is not emulatable and faults the VM.
void dispatch_data_abort(TrapContext* ctx) noexcept {
  const auto da = esr::parse_data_abort(ctx->esr);
  if (!da.isv || da.s1ptw || !esr::is_translation_fault(da.dfsc)) {
    console::write("[trap_handler] unemulatable guest data abort\n");
    dump_trap_context(ctx);
    dispatch_guest_fault(ctx);
    return;
  }

  std::uint64_t hpfar = 0;
  __asm__ volatile("mrs %0, hpfar_el2" : "=r"(hpfar));

  MmioCall call{.ctx     = ctx,
                .ipa     = esr::fault_ipa(hpfar, ctx->far),
                .size    = da.size,
                .write   = da.write,
                .value   = 0,
                .handled = false};
  if (da.write && da.srt != esr::kSrtZeroReg) {
    call.value = esr::extend_mmio_read(ctx->x[da.srt], da.size, false, true); // truncate to size
  }
  cib::service<MmioService>(&call);

  if (!call.handled) {
    console::write("[trap_handler] unclaimed MMIO access at IPA=0x");
    console::write_hex64(call.ipa);
    console::write("\n");
    dump_trap_context(ctx);
    dispatch_guest_fault(ctx);
    return;
  }

  if (!da.write && da.srt != esr::kSrtZeroReg) {
    ctx->x[da.srt] = esr::extend_mmio_read(call.value, da.size, da.sign_extend, da.sixty_four);
  }
  // Unlike HVC, a Data Abort's ELR points AT the faulting instruction:
  // step over it now that the access has been emulated (A64 = 4 bytes).
  ctx->elr += 4;
}

} // namespace

void trap_handler_component::handle_lower_sync(TrapContext* ctx) noexcept {
  switch (esr::get_ec(ctx->esr)) {
  case esr::ExceptionClass::HVC_AA64:
    dispatch_hvc(ctx);
    return;

  case esr::ExceptionClass::DATA_ABORT_LOWER:
    dispatch_data_abort(ctx);
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
