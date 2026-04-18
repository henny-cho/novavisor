#pragma once

// projects/qemu_virt_arm64/include/guest_config.hpp
//
// Static guest descriptor for the QEMU virt AArch64 target.
//
// Phase 5 supports a single guest, identity-mapped through Stage 2.
// These constants are the single source of truth for:
//   - core_mmu  : builds Stage 2 tables covering [kGuestIpaBase, +kGuestIpaSize)
//   - core_vcpu : seeds ELR_EL2/SP_EL1 from kGuestEntry/kGuestStackTop,
//                 tags VTTBR_EL2 with kGuestVmid
//
// Phase 12 (YAML→DTB dynamic provisioning) replaces these compile-time
// constants with a runtime descriptor parsed from hypervisor.dtb.

#include <cstdint>

namespace nova::qemu_virt {

// Guest IPA window.
// QEMU -device loader,file=<binary>,addr=0x50000000,force-raw=on places
// the guest binary at PA 0x5000_0000; Stage 2 identity-maps that PA
// back into IPA 0x5000_0000 for the single VM.
inline constexpr std::uint64_t kGuestIpaBase = 0x5000'0000ULL;
inline constexpr std::uint64_t kGuestIpaSize = 0x0010'0000ULL; // 1 MiB

// EL1 entry PC. The demo's linker.ld places .text.start at IPA base.
inline constexpr std::uint64_t kGuestEntry = kGuestIpaBase;

// Initial SP_EL1. Stack grows down from top of the IPA window.
// demo/common/startup.S resolves __stack_top from its own linker script;
// this constant is the hypervisor's view, matched at build time.
inline constexpr std::uint64_t kGuestStackTop = kGuestIpaBase + kGuestIpaSize;

// VMID tag for VTTBR_EL2. 0 is reserved; Phase 5 uses 1 for the sole VM.
inline constexpr std::uint16_t kGuestVmid = 1;

} // namespace nova::qemu_virt
