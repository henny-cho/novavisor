#pragma once

// Pure register selection for standard shared peripheral interrupts.

#include "nova/arch/gicv3/regs.h"

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

// Bank arithmetic at the boundaries where the word changes: the first
// shared INTID is bit 0 of word 1 (word 0 covers private INTIDs and
// belongs to the redistributor), the last INTID of that word is bit 31,
// and the next one restarts at bit 0 of the following word. ICFGR banks
// half as many INTIDs because it spends two bits on each; IROUTER
// spends a whole 64-bit entry.
static_assert(
    [] {
      const SpiRegisters first = spi_registers(kSpiIntidBase);
      const SpiRegisters last  = spi_registers(kSpiIntidBase + kIntidsPerBank - 1);
      const SpiRegisters next  = spi_registers(kSpiIntidBase + kIntidsPerBank);
      // Two INTIDs sharing one ICFGR word, neither of them at its start:
      // the config bit is the odd bit of the INTID's own pair, and only
      // an INTID away from a word boundary tells that apart from the
      // pair index.
      const SpiRegisters cfg_lo = spi_registers(106);
      const SpiRegisters cfg_hi = spi_registers(109);
      return first.valid && last.valid && next.valid && cfg_lo.valid && cfg_hi.valid && //
             first.bit == 1U && last.bit == 1U << 31U && next.bit == 1U &&              //
             first.group_offset == NOVA_GICD_IGROUPR + kBankStride &&                   //
             first.grpmod_offset == NOVA_GICD_IGRPMODR + kBankStride &&                 //
             first.enable_offset == NOVA_GICD_ISENABLER + kBankStride &&                //
             first.disable_offset == NOVA_GICD_ICENABLER + kBankStride &&               //
             first.clear_offset == NOVA_GICD_ICPENDR + kBankStride &&                   //
             first.deactive_offset == NOVA_GICD_ICACTIVER + kBankStride &&              //
             first.config_offset == NOVA_GICD_ICFGR + 2U * kBankStride && first.edge_bit == 1U << 1U &&
             cfg_lo.config_offset == NOVA_GICD_ICFGR + 6U * kBankStride && // 106 / 16 = word 6
             cfg_hi.config_offset == cfg_lo.config_offset &&               // 109 lands in it too
             cfg_lo.edge_bit == 1U << 21U &&                               // (106 % 16) * 2 + 1
             cfg_hi.edge_bit == 1U << 27U &&                               // (109 % 16) * 2 + 1
             first.route_offset == NOVA_GICD_IROUTER + kSpiIntidBase * kRouteStride &&
             last.enable_offset == first.enable_offset &&                                     // same word
             last.route_offset == first.route_offset + (kIntidsPerBank - 1) * kRouteStride && //
             next.enable_offset == first.enable_offset + kBankStride &&                       // next word
             next.config_offset == first.config_offset + 2U * kBankStride &&
             next.edge_bit == first.edge_bit && // and its own bit 0
             next.route_offset == first.route_offset + kIntidsPerBank * kRouteStride;
    }(),
    "an INTID selects one bit of one bank word in every distributor array");

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
