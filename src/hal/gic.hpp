#pragma once

// hal/gic.hpp
//
// Board-agnostic interrupt-controller facade — the ONE place where
// generic code binds to the active board's GIC (same pattern as
// hal/console.hpp): components include this header, never a
// hal/board_*/ one.
//
// LR bit encoding and injection policy live in the pure model
// (components/vgic/include/vgic_model.hpp); this facade only moves raw
// values between that model and the hardware.

#include "hal/board_qemu_virt/include/gicv3.hpp"

#include <cstddef>
#include <cstdint>

namespace nova::gic {

// INTIDs 1020..1023 are architecturally special (spurious et al.) —
// never dispatch or EOI them.
inline constexpr std::uint32_t kSpecialIntidBase = 1020;

// vGIC maintenance interrupt (standard SBSA PPI assignment).
inline constexpr std::uint32_t kMaintenanceIntid = 25;

// Emulated GIC frames sit at the board's physical addresses so the
// guest sees the same memory map a DTB would advertise; the IPAs are
// left unmapped in Stage 2 on purpose (accesses trap into vgic).
inline constexpr std::uint64_t kGicdIpaBase = board::qemu_virt::GICD_BASE;
inline constexpr std::uint64_t kGicrIpaBase = board::qemu_virt::GICR_BASE;

// ICH_HCR_EL2 / ICH_VMCR_EL2 values banked per VCPU by vgic.
inline constexpr std::uint64_t kIchHcrEn  = board::qemu_virt::gicv3::kIchHcrEn;
inline constexpr std::uint64_t kIchHcrUie = board::qemu_virt::gicv3::kIchHcrUie;
inline constexpr std::uint64_t kVmcrReset =
    board::qemu_virt::gicv3::kIchVmcrVpmrAll | board::qemu_virt::gicv3::kIchVmcrVeng1;

// One-time bring-up: distributor + redistributor + physical CPU
// interface + EL2 virtual CPU interface.
inline void init() noexcept {
  board::qemu_virt::gicv3::distributor_init();
  board::qemu_virt::gicv3::cpu_interface_init();
  board::qemu_virt::gicv3::virtual_interface_init();
}

// Enable a private interrupt (SGI/PPI, INTID 0..31) for this PE.
inline void enable_ppi(std::uint32_t intid) noexcept {
  board::qemu_virt::gicv3::enable_ppi(intid);
}

// Physical interrupt handshake for the EL2 IRQ handler.
inline auto ack() noexcept -> std::uint32_t {
  return board::qemu_virt::gicv3::ack();
}

inline void eoi(std::uint32_t intid) noexcept {
  board::qemu_virt::gicv3::eoi(intid);
}

// Virtual CPU interface state moved on VCPU switches and LR refills.
inline auto lr_count() noexcept -> std::size_t {
  return board::qemu_virt::gicv3::list_register_count();
}

inline auto read_lr(std::size_t index) noexcept -> std::uint64_t {
  return board::qemu_virt::gicv3::read_lr(index);
}

inline void write_lr(std::size_t index, std::uint64_t value) noexcept {
  board::qemu_virt::gicv3::write_lr(index, value);
}

inline auto read_vmcr() noexcept -> std::uint64_t {
  return board::qemu_virt::gicv3::read_vmcr();
}

inline void write_vmcr(std::uint64_t value) noexcept {
  board::qemu_virt::gicv3::write_vmcr(value);
}

inline auto read_hcr() noexcept -> std::uint64_t {
  return board::qemu_virt::gicv3::read_hcr();
}

inline void write_hcr(std::uint64_t value) noexcept {
  board::qemu_virt::gicv3::write_hcr(value);
}

} // namespace nova::gic
