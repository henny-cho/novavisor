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

// Entry [0] boots automatically; the rest stay off until a guest
// issues HVC_VM_START. Every slot sees the same IPA window and differs
// only in PA slot, VMID (0 is reserved), and affinity core. A demo
// selects slots — and thereby cores — through its manifest load_addr:
// slots 0/1 stay on core 0 (single-core demos unchanged), slots 2/3
// put a guest on core 1.
constexpr auto make_guest(std::size_t index, std::uint8_t cpu) noexcept -> GuestDescriptor {
  return GuestDescriptor{
      .ipa_base  = qemu_virt::kGuestIpaBase,
      .ipa_size  = qemu_virt::kGuestIpaSize,
      .load_pa   = qemu_virt::guest_slot_pa(index),
      .entry_pc  = qemu_virt::kGuestEntry,
      .stack_top = qemu_virt::kGuestStackTop,
      .vmid      = static_cast<std::uint16_t>(index + 1),
      .cpu       = cpu,
  };
}

constexpr std::array kGuestTable{make_guest(0, 0), make_guest(1, 0), make_guest(2, 1), make_guest(3, 1)};

static_assert(kGuestTable.size() <= kMaxGuests);
static_assert(kGuestTable[0].cpu == 0, "the boot guest belongs to the primary core");

} // namespace

auto guest_table() noexcept -> std::span<const GuestDescriptor> {
  return kGuestTable;
}

} // namespace nova
