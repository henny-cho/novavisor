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
#include "hal/panic.hpp"
#include "nova/arch/esr.hpp"
#include "nova/arch/sysreg_trap.hpp"
#include "nova/arch/trap_context.hpp"
#include "nova/panic.hpp"
#include "trace/trace.hpp"
#include "trap_handler/elr_policy.hpp"
#include "trap_handler/fp_simd.hpp"
#include "trap_handler/sysreg.hpp"
#include "trap_handler/trap_handler.hpp"
#include "trap_handler/wfx.hpp"

#include <array>
#include <cib/top.hpp>
#include <cstdint>
#include <string_view>

namespace nova {

namespace {

// A guest can call any function ID as fast as it likes, so an
// unconditional log line per call lets one VM flood the shared console
// (Linux probes some IDs on every context switch). Report each ID once.
std::array<std::uint32_t, 8> g_unknown_reported{};

void report_unknown_hvc(std::uint32_t func_id) noexcept {
  for (const std::uint32_t seen : g_unknown_reported) {
    if (seen == func_id) {
      return;
    }
  }
  for (std::uint32_t& slot : g_unknown_reported) {
    if (slot == 0U) {
      slot = func_id;
      break;
    }
  }
  console::line("[trap_handler] unknown HVC func_id=0x", console::Hex{func_id}, " — SMCCC NOT_SUPPORTED\n");
}

void dispatch_hvc(TrapContext* ctx) noexcept {
  // SMCCC: the function ID lives in x0 (bits 31:0); the `hvc #imm16`
  // instruction's own immediate (ESR_EL2.ISS) is conventionally 0 and
  // is NOT the function selector.
  //
  // Shared with the SMC conduit, which the router has already stepped
  // over; an HVC needs no advance at all. Handlers that halt (HVC_EXIT)
  // never return through this path anyway.
  static_assert(trap::elr_policy(esr::ExceptionClass::kHvcAa64) == trap::ElrAdvance::kNone &&
                trap::elr_policy(esr::ExceptionClass::kSmcAa64) == trap::ElrAdvance::kBeforeDispatch);
  HvcCall call{.ctx = ctx, .func_id = static_cast<std::uint32_t>(ctx->x[0]), .handled = false};
  cib::service<HvcService>(&call);

  if (!call.handled) {
    // SMCCC fast calls return in x0–x3; leaving x1–x3 with the guest's
    // arguments violates the convention a caller may rely on.
    ctx->x[0] = kSmcccNotSupported;
    ctx->x[1] = 0;
    ctx->x[2] = 0;
    ctx->x[3] = 0;
    report_unknown_hvc(call.func_id);
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
  // Before the ELR policy moves anything: the syndrome and fault address
  // as the exception arrived, not as the handler left them.
  trace_emit(NOVA_TRACE_EV_TRAP, static_cast<std::uint32_t>(ec), ctx->esr, ctx->far);

  // Classes whose ELR must be stepped over before their handler runs
  // (trap_handler/elr_policy.hpp owns the full matrix). Exactly the two
  // conduits that arrive with ELR at the trapped instruction qualify —
  // stepping an HVC here would make the guest skip the instruction after
  // it, and stepping a data abort would skip the access being emulated.
  static_assert(trap::elr_policy(esr::ExceptionClass::kSmcAa64) == trap::ElrAdvance::kBeforeDispatch &&
                trap::elr_policy(esr::ExceptionClass::kWfx) == trap::ElrAdvance::kBeforeDispatch &&
                trap::elr_policy(esr::ExceptionClass::kHvcAa64) != trap::ElrAdvance::kBeforeDispatch &&
                trap::elr_policy(esr::ExceptionClass::kFpSimd) != trap::ElrAdvance::kBeforeDispatch &&
                trap::elr_policy(esr::ExceptionClass::kMsrMrs) != trap::ElrAdvance::kBeforeDispatch &&
                trap::elr_policy(esr::ExceptionClass::kDataAbortLower) == trap::ElrAdvance::kPerHandler);
  if (trap::elr_policy(ec) == trap::ElrAdvance::kBeforeDispatch) {
    ctx->elr += 4;
  }

  switch (ec) {
  case esr::ExceptionClass::kHvcAa64:
  case esr::ExceptionClass::kSmcAa64:
    // Both SMCCC conduits fan out to the same subscribers — HCR_EL2.TSC
    // traps the guest's SMC so it never reaches firmware at EL3.
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

  // Nothing below is routed, so nothing below resumes the instruction:
  // guest classes are isolated through GuestFaultService and EL2-origin
  // ones panic. Either way the resume rule is kFault.
  static_assert(trap::elr_policy(esr::ExceptionClass::kInstAbortLower) == trap::ElrAdvance::kFault &&
                trap::elr_policy(esr::ExceptionClass::kSvcAa64) == trap::ElrAdvance::kFault &&
                trap::elr_policy(esr::ExceptionClass::kSve) == trap::ElrAdvance::kFault &&
                trap::elr_policy(esr::ExceptionClass::kBrk) == trap::ElrAdvance::kFault &&
                trap::elr_policy(esr::ExceptionClass::kUnknown) == trap::ElrAdvance::kFault &&
                trap::elr_policy(esr::ExceptionClass::kDataAbortCurrent) == trap::ElrAdvance::kFault &&
                trap::elr_policy(esr::ExceptionClass::kSerror) == trap::ElrAdvance::kFault);
  if (esr::is_lower_sync_guest_fault(ec)) {
    console::write("[trap_handler] unhandled guest synchronous exception\n");
    dump_trap_context(ctx);
    trap::dispatch_guest_fault(ctx);
    return;
  }

  if (panic::enter() != panic::Role::kFirst) {
    halt(); // someone else already owns the report (or we re-faulted)
  }
  console::write("[NOVA PANIC] inconsistent lower-EL exception class\n");
  dump_trap_context(ctx);
  halt();
}

namespace {

// Slot names, indexed by vector offset / 0x80 (see vec.S).
constexpr std::array<std::string_view, 16> kVectorNames{
    "el2t_sync",  "el2t_irq",  "el2t_fiq",  "el2t_serror",  "el2h_sync", "el2h_irq", "el2h_fiq", "el2h_serror",
    "lower_sync", "lower_irq", "lower_fiq", "lower_serror", "a32_sync",  "a32_irq",  "a32_fiq",  "a32_serror",
};

} // namespace

} // namespace nova

// ---------------------------------------------------------------------------
// extern "C" entry points — called directly from vec.S
// ---------------------------------------------------------------------------

extern "C" {

void el2_trap_lower_sync(nova::TrapContext* ctx) noexcept {
  cib::service<nova::EL2SyncTrapService>(ctx);
}

// Every vector with no recovery path funnels here (vec.S TRAP_FATAL_BODY).
// Claim the machine first: the first failure owns the console raw, every
// other core parks — a real board's serial log must end with exactly one
// attributable report, not an interleave of neighbors and watchdog resets.
void el2_trap_fatal(nova::TrapContext* ctx, std::uint64_t vector) noexcept {
  switch (nova::panic::enter()) {
  case nova::panic::Role::kRecursive:
    // The report path itself faulted — say so raw and stop digging.
    nova::console::write("\n[NOVA PANIC] recursive fault inside the panic path\n");
    nova::halt();
  case nova::panic::Role::kBystander:
    nova::halt(); // the first failure owns the log
  case nova::panic::Role::kFirst:
    break;
  }
  nova::console::write("\n[NOVA PANIC] EL2 fatal exception: vector ");
  nova::console::write(vector < nova::kVectorNames.size() ? nova::kVectorNames[vector] : "?");
  nova::console::write("\n");
  nova::dump_trap_context(ctx);
  nova::halt();
}

} // extern "C"
