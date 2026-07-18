#pragma once

// hal/gic.hpp
//
// Board-agnostic interrupt-controller facade — the ONE place where
// generic code binds to the active board's GIC (same pattern as
// hal/console.hpp): components include this header, never a
// hal/board_*/ one.

#include "hal/board_qemu_virt/include/gicv3.hpp"

#include <cstdint>

namespace nova::gic {

// INTIDs 1020..1023 are architecturally special (spurious et al.) —
// never dispatch or EOI them.
inline constexpr std::uint32_t kSpecialIntidBase = 1020;

// Priority for injected vIRQs; anything below the guest's PMR
// (preset to accept-all) is deliverable.
inline constexpr std::uint64_t kVirqPriority = 0x80;

// ICH_LR<n>_EL2 fields (Arm IHI 0069, §9.4.6).
inline constexpr std::uint64_t kLrStatePending  = 1ULL << 62;
inline constexpr std::uint64_t kLrGroup1        = 1ULL << 60;
inline constexpr std::uint64_t kLrPriorityShift = 48;

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

// Software-inject a Group 1 virtual interrupt as pending. The guest
// takes it as soon as it runs with vIRQs enabled and unmasked; it EOIs
// through its (hardware-virtualized) ICV_* interface with no further
// hypervisor involvement.
inline void inject_virq(std::uint32_t vintid) noexcept {
  board::qemu_virt::gicv3::write_lr0(kLrStatePending | kLrGroup1 | (kVirqPriority << kLrPriorityShift) | vintid);
}

} // namespace nova::gic
