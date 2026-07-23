#pragma once

#include "core_gic/core_gic.hpp"

#include <cib/top.hpp>
#include <flow/flow.hpp>

namespace nova::smmu {

void init() noexcept;
void handle_irq(IrqCall* call) noexcept;

} // namespace nova::smmu

namespace nova {

struct smmu_component {
  constexpr static auto INIT = flow::action<"smmu_init">([]() noexcept { smmu::init(); });

  static void handle_irq(IrqCall* call) noexcept { smmu::handle_irq(call); }

  constexpr static auto config = cib::config(cib::extend<cib::RuntimeStart>(core_gic_component::INIT >> *INIT),
                                             cib::extend<IrqService>(&smmu_component::handle_irq));
};

} // namespace nova
