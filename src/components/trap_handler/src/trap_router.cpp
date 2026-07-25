// components/trap_handler/src/trap_router.cpp
//
// Implements:
//   - C extern "C" entry points called from vec.S
//   - trap_handler_component::handle_lower_sync (EC-class router)
//
// Per-class dispatch lives in its own TU (data_abort.cpp); the register
// dump in trap_dump.cpp.

#include "dispatch.hpp"
#include "hal/console.hpp"
#include "nova/arch/esr.hpp"
#include "nova/arch/sysreg_trap.hpp"
#include "nova/arch/trap_context.hpp"
#include "nova/panic.hpp"
#include "trap_handler/elr_policy.hpp"
#include "trap_handler/fp_simd.hpp"
#include "trap_handler/sysreg.hpp"
#include "trap_handler/trap_handler.hpp"
#include "trap_handler/wfx.hpp"

#include <cib/top.hpp>
#include <cstdint>

namespace nova {

namespace {

void dispatch_hvc(TrapContext* ctx) noexcept {
  // SMCCC: the function ID lives in x0 (bits 31:0); the `hvc #imm16`
  // instruction's own immediate (ESR_EL2.ISS) is conventionally 0 and
  // is NOT the function selector.
  //
  // Shared with the SMC conduit, which the router has already stepped
  // over; an HVC needs no advance at all. Handlers that halt (HVC_EXIT)
  // never return through this path anyway.
  static_assert(trap::elr_policy(esr::ExceptionClass::kHvcAa64) == trap::ElrAdvance::kNone);
  HvcCall call{.ctx = ctx, .func_id = static_cast<std::uint32_t>(ctx->x[0]), .handled = false};
  cib::service<HvcService>(&call);

  if (!call.handled) {
    console::write("[trap_handler] unknown HVC func_id=0x");
    console::write_hex64(call.func_id);
    console::write(" — returning SMCCC NOT_SUPPORTED\n");
    ctx->x[0] = kSmcccNotSupported;
  }
}

void dispatch_wfx(TrapContext* ctx) noexcept {
  static_assert(trap::elr_policy(esr::ExceptionClass::kWfx) == trap::ElrAdvance::kBeforeDispatch);
  WfxCall call{.ctx = ctx, .is_wfe = (ctx->esr & esr::kWfxTiWfe) != 0, .handled = false};
  cib::service<WfxService>(&call);

  if (!call.handled) {
    console::write("[trap_handler] unhandled WFx — treated as NOP\n");
  }
}

void dispatch_fp_simd(TrapContext* ctx) noexcept {
  static_assert(trap::elr_policy(esr::ExceptionClass::kFpSimd) == trap::ElrAdvance::kNever);
  FpSimdCall call{.ctx = ctx, .handled = false};
  cib::service<FpSimdService>(&call);

  if (!call.handled) {
    // Returning unhandled would re-trap forever — fault the VM instead.
    console::write("[trap_handler] unclaimed FP/SIMD trap\n");
    dump_trap_context(ctx);
    trap::dispatch_guest_fault(ctx);
  }
}

void dispatch_sysreg(TrapContext* ctx) noexcept {
  SysregCall call{.ctx = ctx, .sysreg = esr::parse_sysreg_trap(ctx->esr), .handled = false};
  cib::service<SysregService>(&call);

  if (!call.handled) {
    console::write("[trap_handler] unclaimed guest sysreg trap\n");
    dump_trap_context(ctx);
    trap::dispatch_guest_fault(ctx);
    return;
  }

  // ELR points AT the trapped MSR/MRS. Advance only after successful
  // emulation so fault diagnostics retain the offending instruction.
  static_assert(trap::elr_policy(esr::ExceptionClass::kMsrMrs) == trap::ElrAdvance::kOnClaim);
  ctx->elr += 4;
}

} // namespace

// EC-class router for lower-EL synchronous exceptions. Each supported
// class gets a case that forwards to its dispatch; unhandled guest
// exceptions are isolated through GuestFaultService.
void trap_handler_component::handle_lower_sync(TrapContext* ctx) noexcept {
  const auto ec = esr::get_ec(ctx->esr);

  // Classes whose ELR must be stepped over before their handler runs
  // (trap_handler/elr_policy.hpp owns the full matrix).
  if (trap::elr_policy(ec) == trap::ElrAdvance::kBeforeDispatch) {
    ctx->elr += 4;
  }

  switch (ec) {
  case esr::ExceptionClass::kHvcAa64:
  case esr::ExceptionClass::kSmcAa64:
    // Both SMCCC conduits fan out to the same subscribers (SMC is the
    // second PSCI conduit via HCR_EL2.TSC; no EL3 on this board).
    dispatch_hvc(ctx);
    return;

  case esr::ExceptionClass::kWfx:
    dispatch_wfx(ctx);
    return;

  case esr::ExceptionClass::kFpSimd:
    dispatch_fp_simd(ctx);
    return;

  case esr::ExceptionClass::kMsrMrs:
    dispatch_sysreg(ctx);
    return;

  case esr::ExceptionClass::kDataAbortLower:
    trap::dispatch_data_abort(ctx);
    return;

  default:
    break;
  }

  if (esr::is_lower_sync_guest_fault(ec)) {
    console::write("[trap_handler] unhandled guest synchronous exception\n");
    dump_trap_context(ctx);
    trap::dispatch_guest_fault(ctx);
    return;
  }

  console::write("[NOVA PANIC] inconsistent lower-EL exception class\n");
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
