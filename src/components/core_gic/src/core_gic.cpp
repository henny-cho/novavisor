// components/core_gic/src/core_gic.cpp
//
// el2_trap_irq — called from vec.S (+0x480 lower-EL IRQ vector) whenever
// a physical IRQ is taken to EL2 while the guest runs (HCR_EL2.IMO=1).

#include "components/core_gic/include/core_gic.hpp"

#include "hal/console.hpp"
#include "hal/gic.hpp"
#include "nova/arch/trap_context.hpp"

#include <cib/top.hpp>
#include <cstdint>

extern "C" void el2_trap_irq(nova::TrapContext* /*ctx*/) noexcept {
  const auto intid = nova::gic::ack();
  if (intid >= nova::gic::kSpecialIntidBase) {
    return; // spurious — nothing to dispatch or EOI
  }

  nova::IrqCall call{.intid = intid, .handled = false};
  cib::service<nova::IrqService>(&call);

  if (!call.handled) {
    nova::console::write("[core_gic] unhandled IRQ INTID=");
    nova::console::write_dec64(call.intid);
    nova::console::write("\n");
  }

  nova::gic::eoi(intid);
}
