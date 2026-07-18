// components/trap_handler/src/data_abort.cpp
//
// Stage 2 Data Abort dispatch — the MMIO emulation path. The syndrome
// decode itself is pure (nova/arch/data_abort.hpp, host-tested); this
// TU wires it to MmioService and the guest-fault escalation.

#include "nova/arch/data_abort.hpp"

#include "dispatch.hpp"
#include "hal/console.hpp"
#include "nova_panic/nova_panic.hpp"
#include "trap_handler/guest_fault.hpp"
#include "trap_handler/mmio.hpp"
#include "trap_handler/trap_handler.hpp"

#include <cib/top.hpp>
#include <cstdint>

namespace nova::trap {

void dispatch_guest_fault(TrapContext* ctx) noexcept {
  GuestFaultCall call{.ctx = ctx, .handled = false};
  cib::service<GuestFaultService>(&call);
  if (!call.handled) {
    halt();
  }
}

// Anything without a full syndrome (ISV=0: load/store pair, writeback,
// ...) or that is not a plain translation fault is not emulatable and
// faults the VM.
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

} // namespace nova::trap
