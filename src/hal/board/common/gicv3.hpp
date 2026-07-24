#pragma once

#include "nova/arch/gicv3_regs.h"
#include "nova/arch/gicv3_spi.hpp"

#include <cstddef>
#include <cstdint>

namespace nova::board::common {

template <typename Config>
struct Gicv3 {
  static auto mmio32(std::uintptr_t address) noexcept -> volatile std::uint32_t* {
    return reinterpret_cast<volatile std::uint32_t*>(address);
  }

  static void wait_for_rwp() noexcept {
    while ((*mmio32(Config::kDistributorBase + NOVA_GICD_CTLR) & NOVA_GICD_CTLR_RWP) != 0U) {
    }
  }

  static auto redistributor_frame() noexcept -> std::uintptr_t {
    std::uint64_t mpidr = 0;
    __asm__ volatile("mrs %0, mpidr_el1" : "=r"(mpidr));
    const auto affinity = static_cast<std::uint32_t>(((mpidr >> 32U) & 0xFFU) << 24U | (mpidr & 0x00FFFFFFU));

    std::uintptr_t frame = Config::kRedistributorBase;
    for (std::size_t index = 0; index < Config::kCpuAffinity.size(); ++index) {
      const std::uint32_t typer_lo = *mmio32(frame + NOVA_GICR_TYPER);
      const std::uint32_t typer_hi = *mmio32(frame + NOVA_GICR_TYPER_HI);
      if (typer_hi == affinity) {
        return frame;
      }
      if ((typer_lo & NOVA_GICR_TYPER_LAST) != 0U) {
        break;
      }
      frame += NOVA_GICR_FRAME_SIZE;
    }
    __builtin_trap();
  }

  static void distributor_init() noexcept {
    *mmio32(Config::kDistributorBase + NOVA_GICD_CTLR) = NOVA_GICD_CTLR_ARE;
    wait_for_rwp();
    *mmio32(Config::kDistributorBase + NOVA_GICD_CTLR) = NOVA_GICD_CTLR_ARE | NOVA_GICD_CTLR_ENABLE_GRP1;
    __asm__ volatile("dsb sy" ::: "memory");
  }

  static void redistributor_init() noexcept {
    const std::uintptr_t frame = redistributor_frame();
    auto* const          waker = mmio32(frame + NOVA_GICR_WAKER);
    *waker                     = *waker & ~NOVA_GICR_WAKER_PROCESSOR_SLEEP;
    while ((*waker & NOVA_GICR_WAKER_CHILDREN_ASLEEP) != 0U) {
    }
    *mmio32(frame + NOVA_GICR_IGROUPR0) = ~0U;
  }

  static void enable_ppi(std::uint32_t intid) noexcept {
    *mmio32(redistributor_frame() + NOVA_GICR_ISENABLER0) = 1U << intid;
  }

  static auto configure_spi(std::uint32_t intid, std::uint32_t core, arch::gicv3::SpiTrigger trigger) noexcept -> bool {
    const arch::gicv3::SpiRegisters registers = arch::gicv3::spi_registers(intid);
    const std::uint32_t             typer     = *mmio32(Config::kDistributorBase + NOVA_GICD_TYPER);
    if (!registers.valid || !arch::gicv3::spi_implemented(intid, typer) || core >= Config::kCpuAffinity.size()) {
      return false;
    }

    *mmio32(Config::kDistributorBase + registers.disable_offset) = registers.bit;
    wait_for_rwp();
    *mmio32(Config::kDistributorBase + registers.group_offset) |= registers.bit;
    *reinterpret_cast<volatile std::uint8_t*>(Config::kDistributorBase + NOVA_GICD_IPRIORITYR + intid) =
        arch::gicv3::kDefaultPriority;

    auto* const         config = mmio32(Config::kDistributorBase + registers.config_offset);
    const std::uint32_t edge   = trigger == arch::gicv3::SpiTrigger::kEdge ? registers.edge_bit : 0U;
    *config                    = (*config & ~registers.edge_bit) | edge;

    *reinterpret_cast<volatile std::uint64_t*>(Config::kDistributorBase + registers.route_offset) =
        Config::kCpuAffinity[core];
    __asm__ volatile("dsb sy" ::: "memory");
    return true;
  }

  static auto mask_spi(std::uint32_t intid) noexcept -> bool {
    const arch::gicv3::SpiRegisters registers = arch::gicv3::spi_registers(intid);
    const std::uint32_t             typer     = *mmio32(Config::kDistributorBase + NOVA_GICD_TYPER);
    if (!registers.valid || !arch::gicv3::spi_implemented(intid, typer)) {
      return false;
    }
    *mmio32(Config::kDistributorBase + registers.disable_offset) = registers.bit;
    wait_for_rwp();
    return true;
  }

  static auto unmask_spi(std::uint32_t intid) noexcept -> bool {
    const arch::gicv3::SpiRegisters registers = arch::gicv3::spi_registers(intid);
    const std::uint32_t             typer     = *mmio32(Config::kDistributorBase + NOVA_GICD_TYPER);
    if (!registers.valid || !arch::gicv3::spi_implemented(intid, typer)) {
      return false;
    }
    *mmio32(Config::kDistributorBase + registers.enable_offset) = registers.bit;
    __asm__ volatile("dsb sy" ::: "memory");
    return true;
  }

  static auto clear_pending_spi(std::uint32_t intid) noexcept -> bool {
    const arch::gicv3::SpiRegisters registers = arch::gicv3::spi_registers(intid);
    const std::uint32_t             typer     = *mmio32(Config::kDistributorBase + NOVA_GICD_TYPER);
    if (!registers.valid || !arch::gicv3::spi_implemented(intid, typer)) {
      return false;
    }
    *mmio32(Config::kDistributorBase + registers.clear_offset) = registers.bit;
    __asm__ volatile("dsb sy" ::: "memory");
    return true;
  }

  static auto enable_spi(std::uint32_t intid, std::uint32_t core, arch::gicv3::SpiTrigger trigger) noexcept -> bool {
    return configure_spi(intid, core, trigger) && unmask_spi(intid);
  }

  static auto disable_spi(std::uint32_t intid) noexcept -> bool { return mask_spi(intid); }
};

} // namespace nova::board::common
