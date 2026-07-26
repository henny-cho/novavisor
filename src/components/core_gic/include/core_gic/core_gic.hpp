#pragma once

// components/core_gic/include/core_gic/core_gic.hpp
//
// GICv3 bring-up and EL2 IRQ dispatch.
//
// IrqService: fired for every acknowledged physical INTID. Subscribers
// claim the INTIDs they own by setting `handled` and silently return
// otherwise; unclaimed INTIDs are logged. The dispatcher EOIs after the
// service returns — handlers must not EOI themselves.
//
// Dispatch runs through core_gic::drain, shared by the lower-EL IRQ
// vector (el2_trap_irq) and callers that poll the physical CPU
// interface from EL2 itself (idle loops): ICC_* accesses from EL2 are
// always physical — HCR_EL2.IMO virtualizes EL1 accesses only — so both
// paths use identical mechanics.

#include "hal/console.hpp"
#include "hal/gic.hpp"
#include "nova/panic.hpp"

#include <cib/top.hpp>
#include <cstdint>
#include <flow/flow.hpp>
#include <nexus/callback.hpp>

namespace nova {

struct TrapContext; // nova/arch/trap_context.hpp — carried by pointer only
using IrqEpilogue = void (*)(TrapContext*) noexcept;

struct IrqCall {
  TrapContext*  ctx     = nullptr; // live trap frame (handlers may swap it)
  std::uint32_t intid   = 0;
  bool          handled = false;
};

struct IrqService : public callback::service<IrqCall*> {};

namespace core_gic {

// Request work that must run only after the current physical interrupt
// has been EOI'd. Used when an IRQ retires the resident vCPU: entering
// an idle scheduler from the callback itself would leave the INTID
// active and prevent another interrupt with the same ID from arriving.
void defer_epilogue(IrqEpilogue epilogue) noexcept;

// Ack → IrqService → EOI until no INTID is pending. `ctx` is the live
// trap frame handed to every subscriber.
void drain(TrapContext* ctx) noexcept;

} // namespace core_gic

struct core_gic_component {
  // A missing or unresponsive redistributor means this PE can never
  // take an interrupt — report it here rather than let every later
  // subsystem fail obscurely, and note the security state so a serial
  // log records who owns SPI grouping on this board.
  constexpr static auto INIT = flow::action<"core_gic_init">([]() noexcept {
    const bool ok = gic::init();
    console::line("[core_gic] GICv3 ", (gic::single_security_state() ? "single" : "two"),
                  "-security-state distributor\n");
    if (!ok) {
      console::write("[NOVA PANIC] no usable redistributor for this PE\n");
      halt();
    }
  });

  constexpr static auto config = cib::config(cib::exports<IrqService>, cib::extend<cib::RuntimeStart>(*INIT));
};

} // namespace nova
