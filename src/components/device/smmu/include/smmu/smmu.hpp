#pragma once

#include "core_gic/core_gic.hpp"
#include "smmu/domain_model.hpp"
#include "telemetry/telemetry.hpp"
#include "trap_handler/dma_fault.hpp"

#include <cib/top.hpp>
#include <cstddef>
#include <cstdint>
#include <flow/flow.hpp>
#include <nexus/callback.hpp>

namespace nova::smmu {

void               init() noexcept;
void               handle_irq(IrqCall* call) noexcept;
[[nodiscard]] auto attach_vm(std::size_t vm, std::uint64_t generation) noexcept -> bool;
[[nodiscard]] auto detach_vm(std::size_t vm) noexcept -> bool;
[[nodiscard]] auto quarantine_vm(std::size_t vm) noexcept -> bool;
[[nodiscard]] auto poll_events() noexcept -> std::size_t;
void               telemetry(TelemetryCall* call) noexcept;

} // namespace nova::smmu

namespace nova {

struct smmu_component {
  constexpr static auto INIT = flow::action<"smmu_init">([]() noexcept { smmu::init(); });

  static void handle_irq(IrqCall* call) noexcept { smmu::handle_irq(call); }

  // What this component offers the S layer.
  static void telemetry(TelemetryCall* call) noexcept { smmu::telemetry(call); }

  // init() routes the SMMU event SPIs, so the physical GIC bring-up
  // must have run first — the project nexus orders it. DmaFaultService
  // is published here but exported by trap_handler, so a profile can
  // subscribe to fault recovery without composing this component.
  constexpr static auto config =
      cib::config(cib::extend<cib::RuntimeStart>(*INIT), cib::extend<IrqService>(&smmu_component::handle_irq),
                  cib::extend<TelemetryService>(&smmu_component::telemetry));
};

} // namespace nova
