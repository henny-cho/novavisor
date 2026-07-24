#pragma once

// hal/gic_virt.hpp
//
// EL2 virtual CPU interface facade (ICH_*) — consumed by the vgic
// component only; physical-side components bind through hal/gic.hpp
// and never see these symbols. LR bit encoding and injection policy
// live in the pure model (components/vgic/include/vgic_model.hpp);
// this facade only moves raw values between that model and the
// hardware.

#include "hal/arch/aarch64/gic_ich.hpp"
#include "nova/abi/guest_layout.h"

#include <cstddef>
#include <cstdint>

namespace nova::gic_virt {

// vGIC maintenance interrupt (standard SBSA PPI assignment).
inline constexpr std::uint32_t kMaintenanceIntid = 25;

// Emulated GIC frames — the guest-platform contract fixes the IPAs
// (they equal this board's physical addresses); the IPAs are left
// unmapped in Stage 2 on purpose (accesses trap into vgic).
inline constexpr std::uint64_t kGicdIpaBase = NOVA_GICD_IPA_BASE;
inline constexpr std::uint64_t kGicrIpaBase = NOVA_GICR_IPA_BASE;

// ICH_HCR_EL2 / ICH_VMCR_EL2 values banked per VCPU by vgic. The base
// value keeps SGI-generation writes trapping (vSGI routing) alongside
// the interface enable.
inline constexpr std::uint64_t kIchHcrEn   = arch::gicv3::kIchHcrEn;
inline constexpr std::uint64_t kIchHcrUie  = arch::gicv3::kIchHcrUie;
inline constexpr std::uint64_t kIchHcrBase = arch::gicv3::kIchHcrEn | arch::gicv3::kIchHcrTc;
inline constexpr std::uint64_t kVmcrReset  = arch::gicv3::kIchVmcrVpmrAll | arch::gicv3::kIchVmcrVeng1;

// One-time bring-up of the virtual CPU interface (VMCR reset + HCR.En).
inline void init() noexcept {
  arch::gicv3::virtual_interface_init();
}

// Virtual CPU interface state moved on VCPU switches and LR refills.
inline auto lr_count() noexcept -> std::size_t {
  return arch::gicv3::list_register_count();
}

inline auto read_lr(std::size_t index) noexcept -> std::uint64_t {
  return arch::gicv3::read_lr(index);
}

inline void write_lr(std::size_t index, std::uint64_t value) noexcept {
  arch::gicv3::write_lr(index, value);
}

inline auto read_vmcr() noexcept -> std::uint64_t {
  return arch::gicv3::read_vmcr();
}

inline void write_vmcr(std::uint64_t value) noexcept {
  arch::gicv3::write_vmcr(value);
}

inline auto read_hcr() noexcept -> std::uint64_t {
  return arch::gicv3::read_hcr();
}

inline auto read_misr() noexcept -> std::uint64_t {
  return arch::gicv3::read_misr();
}

inline auto read_eisr() noexcept -> std::uint64_t {
  return arch::gicv3::read_eisr();
}

inline void write_hcr(std::uint64_t value) noexcept {
  arch::gicv3::write_hcr(value);
}

} // namespace nova::gic_virt
