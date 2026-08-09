#pragma once

#include "core_vcpu/core_vcpu.hpp"
#include "nova/abi/dma.hpp"
#include "smp/dma_quiesce.hpp"
#include "telemetry/telemetry.hpp"
#include "vgic/vgic.hpp"

#include <cib/top.hpp>
#include <cstddef>
#include <cstdint>
#include <flow/flow.hpp>

namespace nova::dma_device {

// The protocol's own result type (smp/dma_quiesce.hpp) — this side
// fills it in, VM power reads it, and there is one definition.
using QuiesceResult = DmaQuiesceResult;

void init() noexcept;

// Stop new requests, drain the device, then block its SMMU streams.
// VMs without an assigned board device are unaffected.
[[nodiscard]] auto begin_quiesce(std::size_t vm) noexcept -> QuiesceResult;
[[nodiscard]] auto poll_quiesce(std::size_t vm) noexcept -> QuiesceResult;

// Install the new Stage-2 context before allowing device requests.
[[nodiscard]] auto resume_vm(std::size_t vm, std::uint64_t generation) noexcept -> bool;
[[nodiscard]] auto can_start(std::size_t vm) noexcept -> bool;
[[nodiscard]] auto is_active(std::size_t vm, std::uint64_t generation) noexcept -> bool;
[[nodiscard]] auto start_dma(dma::DeviceId device_id, std::size_t vm, std::uint64_t generation, std::uint64_t source,
                             std::uint64_t destination, std::uint64_t count, bool to_ram) noexcept -> bool;

} // namespace nova::dma_device

namespace nova {

struct dma_device_component {
  static void handle_irq(IrqCall* call) noexcept;
  static void handle_virtual_eoi(VirtualEoiCall* call) noexcept;

  constexpr static auto INIT = flow::action<"dma_device_init">([]() noexcept { dma_device::init(); });

  // Serves the DMA half of VM power. Subscribing rather than being
  // called means a composition without this component simply has no
  // DMA to drain, instead of no VM power at all.
  static void handle_quiesce(DmaQuiesceCall* call) noexcept;

  // Registers vIRQ backends, so it needs vgic and core_vcpu up first —
  // an ordering the project nexus declares.
  // What this component offers the S layer.
  static void telemetry(TelemetryCall* call) noexcept;

  constexpr static auto config =
      cib::config(cib::extend<cib::RuntimeStart>(*INIT), cib::extend<IrqService>(&dma_device_component::handle_irq),
                  cib::extend<VirtualEoiService>(&dma_device_component::handle_virtual_eoi),
                  cib::extend<DmaQuiesceService>(&dma_device_component::handle_quiesce),
                  cib::extend<TelemetryService>(&dma_device_component::telemetry));
};

} // namespace nova
