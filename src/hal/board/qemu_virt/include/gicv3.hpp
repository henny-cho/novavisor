#pragma once

// GICv3 distributor/redistributor MMIO driver for the QEMU virt board
// (-machine gic-version=3) — the board-address-dependent half of the
// GIC. The CPU-interface halves are pure architecture and live in
// hal/arch/aarch64/gic_icc.hpp / gic_ich.hpp. Register offsets and bit
// layouts come from the shared architecture header; only GICD_BASE /
// GICR_BASE come from the board memory map.

#include "board.hpp"
#include "nova/arch/gicv3_regs.h"

#include <cstdint>

namespace nova::board::qemu_virt::gicv3 {

inline auto mmio32(uintptr_t addr) noexcept -> volatile uint32_t* {
  return reinterpret_cast<volatile uint32_t*>(addr);
}

// This core's redistributor frame, found by matching GICR_TYPER's
// affinity field against the caller's MPIDR. Falls back to the last
// frame (TYPER.Last terminates the walk) — unreachable when the GIC
// exposes one redistributor per core, as it must.
inline auto redist_frame() noexcept -> uintptr_t {
  std::uint64_t mpidr = 0;
  __asm__ volatile("mrs %0, mpidr_el1" : "=r"(mpidr));
  const auto affinity = static_cast<uint32_t>(((mpidr >> 32U) & 0xFFU) << 24U | (mpidr & 0x00FFFFFFU));

  uintptr_t frame = GICR_BASE;
  for (;;) {
    const uint32_t typer_lo = *mmio32(frame + NOVA_GICR_TYPER);
    const uint32_t typer_hi = *mmio32(frame + NOVA_GICR_TYPER_HI); // affinity value
    if (typer_hi == affinity || (typer_lo & NOVA_GICR_TYPER_LAST) != 0U) {
      return frame;
    }
    frame += NOVA_GICR_FRAME_SIZE;
  }
}

// Enable Group 1 forwarding at the distributor — system-wide, once,
// before any core wakes its redistributor.
inline void distributor_init() noexcept {
  *mmio32(GICD_BASE + NOVA_GICD_CTLR) = NOVA_GICD_CTLR_ARE;
  *mmio32(GICD_BASE + NOVA_GICD_CTLR) = NOVA_GICD_CTLR_ARE | NOVA_GICD_CTLR_ENABLE_GRP1;
}

// Wake this core's redistributor and put its SGIs/PPIs in Group 1 (the
// group the CPU interface enables). Per-core, on that core.
inline void redistributor_init() noexcept {
  const uintptr_t frame = redist_frame();

  auto* const waker = mmio32(frame + NOVA_GICR_WAKER);
  *waker            = *waker & ~NOVA_GICR_WAKER_PROCESSOR_SLEEP;
  while ((*waker & NOVA_GICR_WAKER_CHILDREN_ASLEEP) != 0U) {
    // wait for the redistributor to wake
  }

  *mmio32(frame + NOVA_GICR_IGROUPR0) = ~0U;
}

// Enable one SGI/PPI (INTID 0..31) at this core's redistributor.
inline void enable_ppi(uint32_t intid) noexcept {
  *mmio32(redist_frame() + NOVA_GICR_ISENABLER0) = 1U << intid;
}

// Route one SPI (INTID 32..63, the first shared word) to a core and
// enable it at the distributor: Group 1, level-triggered reset config,
// IROUTER = the core's Aff0 (QEMU virt cores are flat in Aff0).
// Distributor state is system-wide — call from single-threaded
// bring-up or serialize externally.
inline void enable_spi(uint32_t intid, uint32_t core) noexcept {
  const uint32_t bit = 1U << (intid % 32U);
  *mmio32(GICD_BASE + NOVA_GICD_IGROUPR1) |= bit;
  *reinterpret_cast<volatile uint64_t*>(GICD_BASE + NOVA_GICD_IROUTER + 8U * intid) = core;
  *mmio32(GICD_BASE + NOVA_GICD_ISENABLER1)                                         = bit; // write-1-to-set
}

} // namespace nova::board::qemu_virt::gicv3
