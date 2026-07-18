#pragma once

// nova/guest.hpp
//
// GuestDescriptor: static per-guest configuration, and the contract
// between the project composition (which defines the table) and the
// core components (which consume it).
//
// Components must never include projects/ headers — this header is the
// inverted dependency boundary: core_mmu/core_vcpu read guest_table(),
// and each project links exactly one TU that defines it
// (projects/*/guest_config.cpp).

#include <cstdint>
#include <span>

namespace nova {

struct GuestDescriptor {
  std::uint64_t ipa_base  = 0; // Stage 2 IPA window base (identity-mapped to PA in Phase 5)
  std::uint64_t ipa_size  = 0; // IPA window length in bytes
  std::uint64_t entry_pc  = 0; // initial ELR_EL2 — EL1 entry point
  std::uint64_t stack_top = 0; // initial SP_EL1
  std::uint16_t vmid      = 0; // VTTBR_EL2 VMID tag (0 is reserved — never valid here)
};

// Defined by the active project (projects/*/guest_config.cpp). Never
// empty; entry [0] is the boot guest. Phase 7 (multi-VM) grows this to
// several entries.
auto guest_table() noexcept -> std::span<const GuestDescriptor>;

} // namespace nova
