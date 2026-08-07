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
#include "nova/panic.hpp"
#include "trace/trace.hpp"

#include <array>
#include <cib/top.hpp>
#include <cstdint>

namespace nova::core_gic {
namespace {

std::array<IrqEpilogue, cpu::kMaxCpus> g_epilogue{};

// An INTID nobody claims must not come back. On QEMU only interrupts
// the hypervisor configured can arrive, but a real distributor carries
// whatever firmware or a previous boot left enabled, and a still-
// asserted level source would re-arrive immediately — ack/log/EOI
// forever, at EL2, with interrupts already masked. Quarantine the
// source and log once; the per-INTID latch keeps a storm from
// flooding a shared console.
std::array<std::uint32_t, 4> g_reported{};

void report_once(std::uint32_t intid) noexcept {
  for (const std::uint32_t seen : g_reported) {
    if (seen == intid) {
      return;
    }
  }
  for (std::uint32_t& slot : g_reported) {
    if (slot == 0U) {
      slot = intid;
      break;
    }
  }
  console::line("[core_gic] unclaimed physical IRQ INTID=", console::Dec{intid}, " — quarantined\n");
}

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
    if (intid == kPanicStopSgi) {
      halt(); // another core owns a first-failure report — park silently
    }
    // Every physical interrupt passes here, and this is also where EL2
    // wakes from idle — so it is the definitional gate for the wire
    // between the distributor and a PE, rather than a log line about it.
    trace_emit(NOVA_TRACE_EV_GIC_ACK, intid);

    IrqCall call{.ctx = ctx, .intid = intid, .handled = false};
    cib::service<IrqService>(&call);

    if (!call.handled) {
      report_once(intid);
      gic::quarantine_spi(intid); // no-op for SGI/PPI: those are ours by construction
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
