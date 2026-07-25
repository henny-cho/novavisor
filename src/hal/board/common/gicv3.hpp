#pragma once

#include "hal/arch/aarch64/cpu.hpp"
#include "nova/arch/gicv3_regs.h"
#include "nova/arch/gicv3_spi.hpp"

#include <array>
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

  // GICR_TYPER packs Aff3 into bits 31:24 of the high word, while the
  // MPIDR/IROUTER representation keeps Aff3 at bits 39:32.
  static constexpr auto typer_affinity(std::uint64_t affinity) noexcept -> std::uint32_t {
    return static_cast<std::uint32_t>(((affinity >> 32U) & 0xFFU) << 24U | (affinity & 0x00FFFFFFU));
  }

  static auto find_redistributor_frame() noexcept -> std::uintptr_t {
    const std::uint32_t affinity = typer_affinity(arch::cpu_affinity());

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

  // The frame assignment is fixed hardware — resolve the MMIO walk once
  // per core and serve later calls (enable_ppi runs several times per
  // core during bring-up) from the cache. Frame 0 is never a valid
  // redistributor base, so zero marks "unresolved".
  static auto redistributor_frame() noexcept -> std::uintptr_t {
    static std::array<std::uintptr_t, Config::kCpuAffinity.size()> frames{};

    std::uintptr_t& cached = frames[arch::core_index()];
    if (cached == 0U) {
      cached = find_redistributor_frame();
    }
    return cached;
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

  // GICD_TYPER is boot-constant hardware identification — read it once
  // instead of on every SPI operation (the level-SPI rearm path issues
  // two per guest EOI). Benign if two cores race the first read.
  static auto distributor_typer() noexcept -> std::uint32_t {
    static std::uint32_t typer = 0;
    if (typer == 0U) {
      typer = *mmio32(Config::kDistributorBase + NOVA_GICD_TYPER);
    }
    return typer;
  }

  // Validated register view for one SPI; `valid` is false when the
  // INTID is out of range or unimplemented on this distributor.
  static auto resolve_spi(std::uint32_t intid) noexcept -> arch::gicv3::SpiRegisters {
    const arch::gicv3::SpiRegisters registers = arch::gicv3::spi_registers(intid);
    if (!registers.valid || !arch::gicv3::spi_implemented(intid, distributor_typer())) {
      return {};
    }
    return registers;
  }

  static auto configure_spi(std::uint32_t intid, std::uint32_t core, arch::gicv3::SpiTrigger trigger) noexcept -> bool {
    const arch::gicv3::SpiRegisters registers = resolve_spi(intid);
    if (!registers.valid || core >= Config::kCpuAffinity.size()) {
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
    const arch::gicv3::SpiRegisters registers = resolve_spi(intid);
    if (!registers.valid) {
      return false;
    }
    *mmio32(Config::kDistributorBase + registers.disable_offset) = registers.bit;
    wait_for_rwp();
    return true;
  }

  static auto unmask_spi(std::uint32_t intid) noexcept -> bool {
    const arch::gicv3::SpiRegisters registers = resolve_spi(intid);
    if (!registers.valid) {
      return false;
    }
    *mmio32(Config::kDistributorBase + registers.enable_offset) = registers.bit;
    __asm__ volatile("dsb sy" ::: "memory");
    return true;
  }

  static auto clear_pending_spi(std::uint32_t intid) noexcept -> bool {
    const arch::gicv3::SpiRegisters registers = resolve_spi(intid);
    if (!registers.valid) {
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
