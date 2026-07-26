#pragma once

// Pure register selection for standard shared peripheral interrupts.

#include "nova/arch/gicv3_regs.h"

#include <cstdint>

namespace nova::arch::gicv3 {

inline constexpr std::uint32_t kSpiIntidBase     = 32;
inline constexpr std::uint32_t kSpecialIntidBase = 1020;
inline constexpr std::uint32_t kIntidsPerBank    = 32;
inline constexpr std::uint32_t kBankStride       = sizeof(std::uint32_t);
inline constexpr std::uint32_t kRouteStride      = sizeof(std::uint64_t);
inline constexpr std::uint32_t kTyperLinesMask   = 0x1F;
inline constexpr std::uint8_t  kDefaultPriority  = 0x80;

enum class SpiTrigger : std::uint8_t {
  kLevel,
  kEdge,
};

struct SpiRegisters {
  bool          valid           = false;
  std::uint32_t bit             = 0;
  std::uint32_t group_offset    = 0;
  std::uint32_t grpmod_offset   = 0;
  std::uint32_t enable_offset   = 0;
  std::uint32_t disable_offset  = 0;
  std::uint32_t clear_offset    = 0;
  std::uint32_t deactive_offset = 0;
  std::uint32_t config_offset   = 0;
  std::uint32_t edge_bit        = 0;
  std::uint32_t route_offset    = 0;
};

[[nodiscard]] constexpr auto spi_registers(std::uint32_t intid) noexcept -> SpiRegisters {
  if (intid < kSpiIntidBase || intid >= kSpecialIntidBase) {
    return {};
  }

  const std::uint32_t bank        = intid / kIntidsPerBank;
  const std::uint32_t config_bank = intid / 16U;
  return {
      .valid           = true,
      .bit             = std::uint32_t{1} << (intid % kIntidsPerBank),
      .group_offset    = NOVA_GICD_IGROUPR + bank * kBankStride,
      .grpmod_offset   = NOVA_GICD_IGRPMODR + bank * kBankStride,
      .enable_offset   = NOVA_GICD_ISENABLER + bank * kBankStride,
      .disable_offset  = NOVA_GICD_ICENABLER + bank * kBankStride,
      .clear_offset    = NOVA_GICD_ICPENDR + bank * kBankStride,
      .deactive_offset = NOVA_GICD_ICACTIVER + bank * kBankStride,
      .config_offset   = NOVA_GICD_ICFGR + config_bank * kBankStride,
      .edge_bit        = std::uint32_t{1} << ((intid % 16U) * 2U + 1U),
      .route_offset    = NOVA_GICD_IROUTER + intid * kRouteStride,
  };
}

[[nodiscard]] constexpr auto implemented_intids(std::uint32_t typer) noexcept -> std::uint32_t {
  const std::uint32_t count = ((typer & kTyperLinesMask) + 1U) * kIntidsPerBank;
  return count < kSpecialIntidBase ? count : kSpecialIntidBase;
}

[[nodiscard]] constexpr auto spi_implemented(std::uint32_t intid, std::uint32_t typer) noexcept -> bool {
  return spi_registers(intid).valid && intid < implemented_intids(typer);
}

// Number of 32-INTID banks a distributor implements, including the
// SGI/PPI bank 0 — the sweep bound for inherited-state scrubbing.
[[nodiscard]] constexpr auto implemented_banks(std::uint32_t typer) noexcept -> std::uint32_t {
  return implemented_intids(typer) / kIntidsPerBank;
}

// Redistributor stride: GICv4 parts append VLPI frames to the RD + SGI
// pair, so a fixed 0x20000 walk would land mid-frame on the next PE.
[[nodiscard]] constexpr auto redistributor_stride(std::uint32_t typer_lo) noexcept -> std::uint32_t {
  return (typer_lo & NOVA_GICR_TYPER_VLPIS) != 0U ? NOVA_GICR_FRAME_SIZE4 : NOVA_GICR_FRAME_SIZE;
}

} // namespace nova::arch::gicv3
