#pragma once

// components/vuart/include/vuart/vuart.hpp
//
// Emulated PL011 component — glue between the pure register model
// (vuart_model.hpp) and the rest of the hypervisor:
//
//   - Claims the PL011 frame IPA (left unmapped in Stage 2) through
//     MmioService, for VMs whose descriptor carries UartKind::kVuart.
//   - Guest TX (DR writes) drains into console_mux under the writing
//     vCPU's identity — vuart output is tagged like HVC output.
//   - Claims the physical UART SPI: host RX bytes route through the
//     console_mux input focus into the focused VM's RX FIFO, and a
//     MIS rising edge posts the same SPI number as a vIRQ, targeted
//     by the VM's GICD_IROUTER.
//
// The per-VM UART state is cross-core (RX injection happens on the
// core the physical SPI is routed to, MMIO on the vCPU's core) — one
// spinlock serializes it.

#include "core_gic/core_gic.hpp"
#include "trap_handler/mmio.hpp"

#include <cib/top.hpp>
#include <flow/flow.hpp>

namespace nova::vuart {

// Physical bring-up: unmask the console RX interrupt and route the
// UART SPI to the primary core (the single input consumer).
void init() noexcept;

} // namespace nova::vuart

namespace nova {

struct vuart_component {
  // Claims the PL011 frame for vuart-carrying VMs.
  static void handle_mmio(MmioCall* call) noexcept;

  // Claims the physical UART SPI: drain RX, route by focus, inject.
  static void handle_irq(IrqCall* call) noexcept;

  constexpr static auto INIT = flow::action<"vuart_init">([]() noexcept { vuart::init(); });

  // Explicit flow edge: enable_spi programs the distributor, so the
  // physical GIC bring-up must have run (RuntimeStart order is only
  // guaranteed along >> edges).
  constexpr static auto config = cib::config(cib::extend<cib::RuntimeStart>(core_gic_component::INIT >> *INIT),
                                             cib::extend<MmioService>(&vuart_component::handle_mmio),
                                             cib::extend<IrqService>(&vuart_component::handle_irq));
};

} // namespace nova
