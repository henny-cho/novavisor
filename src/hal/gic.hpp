#pragma once

// hal/gic.hpp
//
// Physical interrupt-controller facade — the ONE place where generic
// code binds to the active board's GIC (same pattern as
// hal/console.hpp): components include this header, never a
// hal/board/*/ one. The EL2 virtual CPU interface has its own facade
// (hal/gic_virt.hpp) so only the vgic component sees it.

#include "hal/arch/aarch64/cpu.hpp"
#include "hal/arch/aarch64/gic/icc.hpp"
#include "hal/board/active/gicv3.hpp"
#include "nova/panic.hpp"

#include <cstddef>
#include <cstdint>

namespace nova::gic {

using SpiTrigger = arch::gicv3::SpiTrigger;

// INTIDs 1020..1023 are architecturally special (spurious et al.) —
// never dispatch or EOI them.
inline constexpr std::uint32_t kSpecialIntidBase = arch::gicv3::kSpecialIntidBase;

// Per-core bring-up: this core's redistributor + physical CPU
// interface. Every core runs it for itself (secondaries via
// smp::secondary_main). The virtual CPU interface is brought up by
// the vgic component through hal/gic_virt.hpp.
// Returns false when this PE's redistributor is missing or does not
// wake — the caller reports it; interrupts cannot work on this core.
inline auto init_cpu() noexcept -> bool {
  const bool ok = board::active::Gicv3::redistributor_init();
  arch::gicv3::cpu_interface_init();
  // The panic-stop SGI must be deliverable on every PE from cold boot —
  // it is what keeps a first failure's report free of neighbor output.
  board::active::Gicv3::enable_ppi(kPanicStopSgi);
  return ok;
}

// Cold-boot bring-up on the primary: the system-wide distributor
// (inherited state scrubbed before Group 1 is enabled), then this
// core's share.
inline auto init() noexcept -> bool {
  board::active::Gicv3::distributor_init();
  return init_cpu();
}

// True when the distributor runs in a single security state. With two
// states the group registers are firmware-owned (RAZ/WI to NS EL2), so
// SPI grouping depends on what the firmware set up.
[[nodiscard]] inline auto single_security_state() noexcept -> bool {
  return board::active::Gicv3::single_security_state();
}

// Disable an INTID nobody claims and clear its pending/active state —
// without this a still-asserted level source re-arrives forever.
inline auto quarantine_spi(std::uint32_t intid) noexcept -> bool {
  return board::active::Gicv3::quarantine_spi(intid);
}

// Enable a private interrupt (SGI/PPI, INTID 0..31) for this PE.
inline void enable_ppi(std::uint32_t intid) noexcept {
  board::active::Gicv3::enable_ppi(intid);
}

// Route a standard shared peripheral interrupt to one core and enable
// it at the distributor. GICD state is system-wide and not serialized.
inline auto enable_spi(std::uint32_t intid, std::size_t target_cpu, SpiTrigger trigger = SpiTrigger::kLevel) noexcept
    -> bool {
  // Bounds live in the driver (configure_spi checks the affinity table).
  return board::active::Gicv3::enable_spi(intid, static_cast<std::uint32_t>(target_cpu), trigger);
}

inline auto disable_spi(std::uint32_t intid) noexcept -> bool {
  return board::active::Gicv3::disable_spi(intid);
}

inline auto configure_spi(std::uint32_t intid, std::size_t target_cpu, SpiTrigger trigger = SpiTrigger::kLevel) noexcept
    -> bool {
  return board::active::Gicv3::configure_spi(intid, static_cast<std::uint32_t>(target_cpu), trigger);
}

inline auto mask_spi(std::uint32_t intid) noexcept -> bool {
  return board::active::Gicv3::mask_spi(intid);
}

inline auto unmask_spi(std::uint32_t intid) noexcept -> bool {
  return board::active::Gicv3::unmask_spi(intid);
}

inline auto clear_pending_spi(std::uint32_t intid) noexcept -> bool {
  return board::active::Gicv3::clear_pending_spi(intid);
}

// Send an SGI to another core (EL2 cross-call IPI).
inline void send_sgi(std::size_t target_cpu, std::uint32_t intid) noexcept {
  if (target_cpu >= board::active::kCpuAffinity.size() || intid >= arch::gicv3::kSgiCount) {
    return;
  }
  const std::uint64_t affinity = board::active::kCpuAffinity[target_cpu];
  if (!arch::gicv3::sgi_target_supported(affinity)) {
    return;
  }
  arch::gicv3::send_sgi(affinity, intid);
}

// Ask every other PE to park at its next trap (hal/panic.hpp). Best
// effort by design: a core spinning in EL2 with interrupts masked will
// not take it, but cores running guests or idling in wfi will.
inline void broadcast_panic_stop() noexcept {
  const std::size_t me = arch::core_index();
  for (std::size_t i = 0; i < board::active::kCpuAffinity.size(); ++i) {
    if (i != me) {
      send_sgi(i, kPanicStopSgi);
    }
  }
}

// Physical interrupt handshake for the EL2 IRQ handler.
inline auto ack() noexcept -> std::uint32_t {
  return arch::gicv3::ack();
}

inline void eoi(std::uint32_t intid) noexcept {
  arch::gicv3::eoi(intid);
}

} // namespace nova::gic
