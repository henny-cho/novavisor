#pragma once

// GICv3 distributor/redistributor MMIO driver for the QEMU virt board
// (-machine gic-version=3) — the board-address-dependent half of the
// GIC. The CPU-interface halves are pure architecture and live in
// hal/arch/aarch64/gic_icc.hpp / gic_ich.hpp. Register offsets and bit
// layouts come from the shared architecture header; only GICD_BASE /
// GICR_BASE come from the board memory map. Single-PE bring-up only —
// Phase 9 (SMP) generalizes the per-CPU redistributor lookup.

#include "board.hpp"
#include "nova/arch/gicv3_regs.h"

#include <cstdint>

namespace nova::board::qemu_virt::gicv3 {

inline auto mmio32(uintptr_t addr) noexcept -> volatile uint32_t* {
  return reinterpret_cast<volatile uint32_t*>(addr);
}

// Enable Group 1 forwarding at the distributor, wake the CPU 0
// redistributor, and put every SGI/PPI in Group 1 (the group the CPU
// interface enables).
inline void distributor_init() noexcept {
  *mmio32(GICD_BASE + NOVA_GICD_CTLR) = NOVA_GICD_CTLR_ARE;
  *mmio32(GICD_BASE + NOVA_GICD_CTLR) = NOVA_GICD_CTLR_ARE | NOVA_GICD_CTLR_ENABLE_GRP1;

  auto* const waker = mmio32(GICR_BASE + NOVA_GICR_WAKER);
  *waker            = *waker & ~NOVA_GICR_WAKER_PROCESSOR_SLEEP;
  while ((*waker & NOVA_GICR_WAKER_CHILDREN_ASLEEP) != 0U) {
    // wait for the redistributor to wake
  }

  *mmio32(GICR_BASE + NOVA_GICR_IGROUPR0) = ~0U;
}

// Enable one SGI/PPI (INTID 0..31) at the redistributor.
inline void enable_ppi(uint32_t intid) noexcept {
  *mmio32(GICR_BASE + NOVA_GICR_ISENABLER0) = 1U << intid;
}

} // namespace nova::board::qemu_virt::gicv3
