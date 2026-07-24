#pragma once

// QEMU educational PCI DMA device access.

#include "board_layout.h"
#include "nova/abi/edu.h"
#include "nova/arch/pci.hpp"

#include <cstdint>

namespace nova::board::qemu_virt::pci_edu {

inline constexpr arch::pci::Bdf kBdf{.bus = 0, .device = 2, .function = 0};
inline constexpr std::uint16_t  kDmaDeviceId    = 0;
inline constexpr std::uint16_t  kStreamId       = arch::pci::requester_id(kBdf);
inline constexpr std::uint32_t  kPciDeviceId    = 0x11E8'1234;
inline constexpr std::uint64_t  kBar0           = NOVA_BOARD_PCIE_MMIO_BASE;
inline constexpr std::uint64_t  kBar0Size       = 0x0010'0000;
inline constexpr std::uint32_t  kPhysicalIntid  = 37;
inline constexpr std::uint64_t  kInternalBuffer = NOVA_EDU_INTERNAL_BUFFER;
inline constexpr std::uint64_t  kBufferSize     = NOVA_EDU_BUFFER_SIZE;

inline constexpr std::uint16_t kPciCommandMemory    = 1U << 1U;
inline constexpr std::uint16_t kPciCommandBusMaster = 1U << 2U;
inline constexpr std::uint64_t kDmaRun              = NOVA_EDU_DMA_RUN;
inline constexpr std::uint64_t kDmaToRam            = NOVA_EDU_DMA_TO_PCI;

namespace reg {
inline constexpr std::uint16_t kId             = 0x00;
inline constexpr std::uint16_t kCommand        = 0x04;
inline constexpr std::uint16_t kBar0           = 0x10;
inline constexpr std::uint64_t kIdentity       = NOVA_EDU_IDENTITY_REG;
inline constexpr std::uint64_t kIrqStatus      = NOVA_EDU_IRQ_STATUS;
inline constexpr std::uint64_t kIrqRaise       = NOVA_EDU_IRQ_RAISE;
inline constexpr std::uint64_t kIrqAcknowledge = NOVA_EDU_IRQ_ACK;
inline constexpr std::uint64_t kDmaSource      = NOVA_EDU_DMA_SOURCE;
inline constexpr std::uint64_t kDmaDestination = NOVA_EDU_DMA_DEST;
inline constexpr std::uint64_t kDmaCount       = NOVA_EDU_DMA_COUNT;
inline constexpr std::uint64_t kDmaCommand     = NOVA_EDU_DMA_COMMAND;
} // namespace reg

[[nodiscard]] inline auto config_address(std::uint16_t offset) noexcept -> std::uint64_t {
  return NOVA_BOARD_PCIE_ECAM_BASE + arch::pci::ecam_offset(kBdf, offset);
}

[[nodiscard]] inline auto read_config32(std::uint16_t offset) noexcept -> std::uint32_t {
  return *reinterpret_cast<volatile std::uint32_t*>(config_address(offset));
}

inline void write_config32(std::uint16_t offset, std::uint32_t value) noexcept {
  *reinterpret_cast<volatile std::uint32_t*>(config_address(offset)) = value;
}

[[nodiscard]] inline auto read_mmio32(std::uint64_t offset) noexcept -> std::uint32_t {
  return *reinterpret_cast<volatile std::uint32_t*>(kBar0 + offset);
}

[[nodiscard]] inline auto read_mmio64(std::uint64_t offset) noexcept -> std::uint64_t {
  return *reinterpret_cast<volatile std::uint64_t*>(kBar0 + offset);
}

inline void write_mmio32(std::uint64_t offset, std::uint32_t value) noexcept {
  *reinterpret_cast<volatile std::uint32_t*>(kBar0 + offset) = value;
}

inline void write_mmio64(std::uint64_t offset, std::uint64_t value) noexcept {
  *reinterpret_cast<volatile std::uint64_t*>(kBar0 + offset) = value;
}

inline void publish_memory() noexcept {
  __asm__ volatile("dsb oshst" ::: "memory");
}

inline void acquire_memory() noexcept {
  __asm__ volatile("dsb osh" ::: "memory");
}

inline void clear_interrupts() noexcept {
  const std::uint32_t pending = read_mmio32(reg::kIrqStatus);
  if (pending != 0U) {
    write_mmio32(reg::kIrqAcknowledge, pending);
    publish_memory();
  }
}

[[nodiscard]] inline auto present() noexcept -> bool {
  return read_config32(reg::kId) == kPciDeviceId;
}

[[nodiscard]] inline auto configure_bar() noexcept -> bool {
  write_config32(reg::kCommand, 0);
  write_config32(reg::kBar0, static_cast<std::uint32_t>(kBar0));
  write_config32(reg::kCommand, kPciCommandMemory);
  publish_memory();
  return read_config32(reg::kBar0) == kBar0 && read_mmio32(reg::kIdentity) == NOVA_EDU_IDENTITY;
}

[[nodiscard]] inline auto enable_bus_master() noexcept -> bool {
  write_config32(reg::kCommand, kPciCommandMemory | kPciCommandBusMaster);
  publish_memory();
  return (read_config32(reg::kCommand) & (kPciCommandMemory | kPciCommandBusMaster)) ==
         (kPciCommandMemory | kPciCommandBusMaster);
}

[[nodiscard]] inline auto disable_bus_master() noexcept -> bool {
  write_config32(reg::kCommand, kPciCommandMemory);
  publish_memory();
  return (read_config32(reg::kCommand) & (kPciCommandMemory | kPciCommandBusMaster)) == kPciCommandMemory;
}

[[nodiscard]] inline auto dma_running() noexcept -> bool {
  return (read_mmio64(reg::kDmaCommand) & kDmaRun) != 0U;
}

[[nodiscard]] inline auto start_dma(std::uint64_t source, std::uint64_t destination, std::uint64_t count,
                                    bool to_ram) noexcept -> bool {
  if (dma_running() || count == 0U || count > kBufferSize) {
    return false;
  }
  const std::uint64_t internal = to_ram ? source : destination;
  if (internal < kInternalBuffer || internal - kInternalBuffer > kBufferSize - count) {
    return false;
  }
  write_mmio64(reg::kDmaSource, source);
  write_mmio64(reg::kDmaDestination, destination);
  write_mmio64(reg::kDmaCount, count);
  write_mmio64(reg::kDmaCommand, kDmaRun | (to_ram ? kDmaToRam : 0U));
  publish_memory();
  return true;
}

} // namespace nova::board::qemu_virt::pci_edu
