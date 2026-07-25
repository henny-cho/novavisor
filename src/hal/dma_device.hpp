#pragma once

// hal/dma_device.hpp
//
// Board-neutral access to the guest-assignable DMA device. This is the
// contract a board must satisfy to carry the DMA components: identity,
// bring-up, bus-master gating, transfer start/drain, interrupt
// acknowledge, and the two memory-ordering barriers around descriptor
// handoff.

#include "hal/board/active/dma_device.hpp"

#include <cstdint>

namespace nova::dma_device::hw::device {

// Stable identity used by the DMA ownership policy tables, and the
// device-internal buffer window transfers stage through.
inline constexpr std::uint16_t kDmaDeviceId    = board::active::pci_edu::kDmaDeviceId;
inline constexpr std::uint64_t kInternalBuffer = board::active::pci_edu::kInternalBuffer;

// Probe: the device answers on its config space.
[[nodiscard]] inline auto present() noexcept -> bool {
  return board::active::pci_edu::present();
}

// One-time bring-up: program the BAR and verify the device responds.
[[nodiscard]] inline auto configure_bar() noexcept -> bool {
  return board::active::pci_edu::configure_bar();
}

// Bus-master gating — the quiesce/resume pair device isolation uses.
[[nodiscard]] inline auto enable_bus_master() noexcept -> bool {
  return board::active::pci_edu::enable_bus_master();
}

[[nodiscard]] inline auto disable_bus_master() noexcept -> bool {
  return board::active::pci_edu::disable_bus_master();
}

// True while a transfer is in flight (the drain predicate).
[[nodiscard]] inline auto dma_running() noexcept -> bool {
  return board::active::pci_edu::dma_running();
}

// Start one transfer. Rejects an in-flight device and any request whose
// device-internal endpoint falls outside kInternalBuffer.
[[nodiscard]] inline auto start_dma(std::uint64_t source, std::uint64_t destination, std::uint64_t count,
                                    bool to_ram) noexcept -> bool {
  return board::active::pci_edu::start_dma(source, destination, count, to_ram);
}

// Acknowledge whatever the device has pending (idempotent).
inline void clear_interrupts() noexcept {
  board::active::pci_edu::clear_interrupts();
}

// Ordering around descriptor handoff: publish before handing memory to
// the device, acquire before reading what it wrote.
inline void publish_memory() noexcept {
  board::active::pci_edu::publish_memory();
}

inline void acquire_memory() noexcept {
  board::active::pci_edu::acquire_memory();
}

} // namespace nova::dma_device::hw::device
