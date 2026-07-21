// components/core_gic/src/core_gic.cpp
//
// core_gic::drain — ack/dispatch/EOI loop over the physical CPU
// interface. el2_trap_irq (vec.S +0x480, lower-EL IRQ vector) is a thin
// wrapper; EL2-resident callers invoke drain directly after a wfi
// wake-up, where pending IRQs exist but no exception is taken because
// PSTATE.I stays masked.

#include "core_gic/core_gic.hpp"

#include "hal/console.hpp"
#include "hal/cpu.hpp"
#include "hal/gic.hpp"
#include "nova/arch/trap_context.hpp"

#include <array>
#include <cib/top.hpp>
#include <cstdint>

namespace nova::core_gic {
namespace {

std::array<IrqEpilogue, cpu::kMaxCpus> g_epilogue{};

} // namespace

void defer_epilogue(IrqEpilogue epilogue) noexcept {
  g_epilogue[cpu::id()] = epilogue;
}

void drain(TrapContext* ctx) noexcept {
  for (;;) {
    const auto intid = gic::ack();
    if (intid >= gic::kSpecialIntidBase) {
      return; // spurious — nothing left to dispatch or EOI
    }

    IrqCall call{.ctx = ctx, .intid = intid, .handled = false};
    cib::service<IrqService>(&call);

    if (!call.handled) {
      console::write("[core_gic] unhandled IRQ INTID=");
      console::write_dec64(call.intid);
      console::write("\n");
    }

    gic::eoi(intid);

    IrqEpilogue epilogue  = g_epilogue[cpu::id()];
    g_epilogue[cpu::id()] = nullptr;
    if (epilogue != nullptr) {
      epilogue(ctx);
    }
  }
}

} // namespace nova::core_gic

extern "C" void el2_trap_irq(nova::TrapContext* ctx) noexcept {
  nova::core_gic::drain(ctx);
}
