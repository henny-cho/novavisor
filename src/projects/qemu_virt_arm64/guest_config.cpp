// projects/qemu_virt_arm64/guest_config.cpp
//
// Defines the nova::guest_table() contract (nova/abi/guest.hpp) for this
// target from the compile-time constants in include/guest_config.hpp.
// This TU is linked directly into novavisor.elf — it is the injection
// point that keeps core components free of projects/ includes.

#include "guest_config.hpp"

#include "nova/abi/guest.hpp"

#include <array>
#include <cstdint>
#include <span>

namespace nova {
namespace {

// Entry [0] boots automatically; entry [1] stays off until a guest
// issues HVC_VM_START (demo/03_ivc_pingpong). Both see the same IPA
// window and differ only in PA slot and VMID (0 is reserved).
constexpr auto make_guest(std::size_t index) noexcept -> GuestDescriptor {
  return GuestDescriptor{
      .ipa_base  = qemu_virt::kGuestIpaBase,
      .ipa_size  = qemu_virt::kGuestIpaSize,
      .load_pa   = qemu_virt::guest_slot_pa(index),
      .entry_pc  = qemu_virt::kGuestEntry,
      .stack_top = qemu_virt::kGuestStackTop,
      .vmid      = static_cast<std::uint16_t>(index + 1),
  };
}

constexpr std::array kGuestTable{make_guest(0), make_guest(1)};

static_assert(kGuestTable.size() <= kMaxGuests);

} // namespace

auto guest_table() noexcept -> std::span<const GuestDescriptor> {
  return kGuestTable;
}

} // namespace nova
