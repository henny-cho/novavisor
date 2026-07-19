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

#include <cstddef>
#include <cstdint>

namespace nova::gic {

// INTIDs 1020..1023 are architecturally special (spurious et al.) —
// never dispatch or EOI them.
inline constexpr std::uint32_t kSpecialIntidBase = 1020;

// Per-core bring-up: this core's redistributor + physical CPU
// interface. Every core runs it for itself (secondaries via
// smp::secondary_main). The virtual CPU interface is brought up by
// the vgic component through hal/gic_virt.hpp.
inline void init_cpu() noexcept {
  board::qemu_virt::gicv3::redistributor_init();
  arch::gicv3::cpu_interface_init();
}

// Cold-boot bring-up on the primary: the system-wide distributor,
// then this core's share.
inline void init() noexcept {
  board::qemu_virt::gicv3::distributor_init();
  init_cpu();
}

// Enable a private interrupt (SGI/PPI, INTID 0..31) for this PE.
inline void enable_ppi(std::uint32_t intid) noexcept {
  board::qemu_virt::gicv3::enable_ppi(intid);
}

// Route a shared peripheral interrupt (SPI, INTID 32..63) to one core
// and enable it at the distributor. Bring-up only — GICD state is
// system-wide and the driver does not serialize.
inline void enable_spi(std::uint32_t intid, std::size_t target_cpu) noexcept {
  board::qemu_virt::gicv3::enable_spi(intid, static_cast<std::uint32_t>(target_cpu));
}

// Send an SGI to another core (EL2 cross-call IPI).
inline void send_sgi(std::size_t target_cpu, std::uint32_t intid) noexcept {
  arch::gicv3::send_sgi(target_cpu, intid);
}

// Physical interrupt handshake for the EL2 IRQ handler.
inline auto ack() noexcept -> std::uint32_t {
  return arch::gicv3::ack();
}

inline void eoi(std::uint32_t intid) noexcept {
  arch::gicv3::eoi(intid);
}

} // namespace nova::gic
