#pragma once

// hal/gic.hpp
//
// Physical interrupt-controller facade — the ONE place where generic
// code binds to the active board's GIC (same pattern as
// hal/console.hpp): components include this header, never a
// hal/board/*/ one. The EL2 virtual CPU interface has its own facade
// (hal/gic_virt.hpp) so only the vgic component sees it.

#include "hal/arch/aarch64/gic_icc.hpp"
#include "hal/board/qemu_virt/include/gicv3.hpp"

#include <cstdint>

namespace nova::gic {

// INTIDs 1020..1023 are architecturally special (spurious et al.) —
// never dispatch or EOI them.
inline constexpr std::uint32_t kSpecialIntidBase = 1020;

// One-time bring-up: distributor + redistributor + physical CPU
// interface. The virtual CPU interface is brought up by the vgic
// component through hal/gic_virt.hpp.
inline void init() noexcept {
  board::qemu_virt::gicv3::distributor_init();
  arch::gicv3::cpu_interface_init();
}

// Enable a private interrupt (SGI/PPI, INTID 0..31) for this PE.
inline void enable_ppi(std::uint32_t intid) noexcept {
  board::qemu_virt::gicv3::enable_ppi(intid);
}

// Physical interrupt handshake for the EL2 IRQ handler.
inline auto ack() noexcept -> std::uint32_t {
  return arch::gicv3::ack();
}

inline void eoi(std::uint32_t intid) noexcept {
  arch::gicv3::eoi(intid);
}

} // namespace nova::gic
