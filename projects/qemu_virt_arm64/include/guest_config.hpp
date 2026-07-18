#pragma once

// projects/qemu_virt_arm64/include/guest_config.hpp
//
// Static guest configuration for the QEMU virt AArch64 target.
//
// Phase 5 supports a single guest, identity-mapped through Stage 2.
// These constants feed the GuestDescriptor table defined in
// ../guest_config.cpp — components consume them only through
// nova::guest_table() (nova/guest.hpp), never by including this header.
//
// Phase 12 (YAML→DTB dynamic provisioning) replaces these compile-time
// constants with a runtime descriptor parsed from hypervisor.dtb.

#include "nova/guest_layout.h"

#include <cstddef>
#include <cstdint>

namespace nova::qemu_virt {

// Guest IPA window, from the layout header shared with the demo guest
// linker script (demo/common/linker.ld.S). Every guest sees (and links
// against) the same window; only the backing PA slot differs.
// QEMU -device loader,file=<binary>,addr=<slot PA>,force-raw=on places
// each guest binary at its slot; Stage 2 maps the window onto it.
inline constexpr std::uint64_t kGuestIpaBase = NOVA_GUEST_IPA_BASE;
inline constexpr std::uint64_t kGuestIpaSize = NOVA_GUEST_IPA_SIZE;

// PA slot for guest i. Slot 0 is identity with the IPA window, so the
// Phase 5/6 single-guest demos keep their manifests unchanged.
[[nodiscard]] constexpr auto guest_slot_pa(std::size_t index) noexcept -> std::uint64_t {
  return NOVA_GUEST_IPA_BASE + (index * NOVA_GUEST_PA_STRIDE);
}

// EL1 entry PC. The demo's linker.ld places .text.start at IPA base.
inline constexpr std::uint64_t kGuestEntry = kGuestIpaBase;

// Initial SP_EL1. Stack grows down from top of the IPA window.
// demo/common/startup.S resolves __stack_top from its own linker script;
// this constant is the hypervisor's view, matched at build time.
inline constexpr std::uint64_t kGuestStackTop = kGuestIpaBase + kGuestIpaSize;

} // namespace nova::qemu_virt
