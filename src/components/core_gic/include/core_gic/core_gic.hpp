#pragma once

// components/core_gic/include/core_gic/core_gic.hpp
//
// GICv3 bring-up and EL2 IRQ dispatch.
//
// IrqService: fired by el2_trap_irq (src/core_gic.cpp) for every
// acknowledged physical INTID. Subscribers claim the INTIDs they own by
// setting `handled` and silently return otherwise; unclaimed INTIDs are
// logged. The dispatcher EOIs after the service returns — handlers must
// not EOI themselves.

#include "hal/gic.hpp"

#include <cib/top.hpp>
#include <cstdint>
#include <flow/flow.hpp>
#include <nexus/callback.hpp>

namespace nova {

struct IrqCall {
  std::uint32_t intid   = 0;
  bool          handled = false;
};

struct IrqService : public callback::service<IrqCall*> {};

struct core_gic_component {
  constexpr static auto INIT = flow::action<"core_gic_init">([]() noexcept { gic::init(); });

  constexpr static auto config = cib::config(cib::exports<IrqService>, cib::extend<cib::RuntimeStart>(*INIT));
};

} // namespace nova
