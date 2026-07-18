#pragma once

// nova/abi/guest.hpp
//
// GuestDescriptor: static per-guest configuration, and the contract
// between the project composition (which defines the table) and the
// core components (which consume it).
//
// Components must never include projects/ headers — this header is the
// inverted dependency boundary: core_mmu/core_vcpu read guest_table(),
// and each project links exactly one TU that defines it
// (projects/*/guest_config.cpp).

#include <cstddef>
#include <cstdint>
#include <span>

namespace nova {

// Compile-time upper bound on guest_table() entries — sizes the static
// per-VM backing stores (Stage 2 table sets, VCPU array).
inline constexpr std::size_t kMaxGuests = 4;

struct GuestDescriptor {
  std::uint64_t ipa_base  = 0; // Stage 2 IPA window base (guest view; same for every guest)
  std::uint64_t ipa_size  = 0; // IPA window length in bytes
  std::uint64_t load_pa   = 0; // PA slot backing the window (Stage 2 output address)
  std::uint64_t entry_pc  = 0; // initial ELR_EL2 — EL1 entry point (IPA)
  std::uint64_t stack_top = 0; // initial SP_EL1 (IPA)
  std::uint16_t vmid      = 0; // VTTBR_EL2 VMID tag (0 is reserved — never valid here)
  std::uint8_t  cpu       = 0; // static affinity: the physical core this VCPU runs on

  // True when [ipa, ipa + len) lies fully inside the guest window.
  // len must not exceed ipa_size (callers clamp first).
  [[nodiscard]] constexpr auto contains(std::uint64_t ipa, std::uint64_t len) const noexcept -> bool {
    return ipa >= ipa_base && ipa <= ipa_base + ipa_size - len;
  }

  // Translate a window IPA to the backing PA (EL2 runs with a flat view
  // of physical memory). Valid only for addresses inside the window.
  [[nodiscard]] constexpr auto to_pa(std::uint64_t ipa) const noexcept -> std::uint64_t {
    return ipa - ipa_base + load_pa;
  }
};

// Defined by the active project (projects/*/guest_config.cpp). Never
// empty, at most kMaxGuests entries; entry [0] is the boot guest
// (affinity core 0), further entries start off and are launched via
// HVC_VM_START. A VCPU executes only on its affinity core; all its
// state is owned (read and written) by that core.
auto guest_table() noexcept -> std::span<const GuestDescriptor>;

} // namespace nova
