// projects/qemu_virt_arm64/guest_config.cpp
//
// Defines the nova::guest_table() contract (nova/guest.hpp) for this
// target from the compile-time constants in include/guest_config.hpp.
// This TU is linked directly into novavisor.elf — it is the injection
// point that keeps core components free of projects/ includes.

#include "projects/qemu_virt_arm64/include/guest_config.hpp"

#include "nova/guest.hpp"

#include <array>
#include <span>

namespace nova {
namespace {

constexpr std::array kGuestTable{
    GuestDescriptor{
        .ipa_base  = qemu_virt::kGuestIpaBase,
        .ipa_size  = qemu_virt::kGuestIpaSize,
        .entry_pc  = qemu_virt::kGuestEntry,
        .stack_top = qemu_virt::kGuestStackTop,
        .vmid      = qemu_virt::kGuestVmid,
    },
};

} // namespace

auto guest_table() noexcept -> std::span<const GuestDescriptor> {
  return kGuestTable;
}

} // namespace nova
