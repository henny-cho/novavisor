#pragma once

// hal/gic_virt.hpp
//
// EL2 virtual CPU interface facade (ICH_*) — consumed by the vgic
// component only; physical-side components bind through hal/gic.hpp
// and never see these symbols. LR bit encoding and injection policy
// live in the pure model (the vgic component);
// this facade only moves raw values between that model and the
// hardware.

#include "hal/arch/aarch64/gic/ich.hpp"

#include <cstddef>
#include <cstdint>

namespace nova::gic_virt {

// vGIC maintenance interrupt (standard SBSA PPI assignment).
inline constexpr std::uint32_t kMaintenanceIntid = 25;

// ICH_HCR_EL2 values banked per VCPU by vgic. The base value keeps
// SGI-generation writes trapping (vSGI routing) alongside the
// interface enable. The VMCR reset value is runtime-derived from
// ICH_VTR (binary-point minimums) — see vmcr_reset() below.
inline constexpr std::uint64_t kIchHcrEn   = arch::gicv3::kIchHcrEn;
inline constexpr std::uint64_t kIchHcrUie  = arch::gicv3::kIchHcrUie;
inline constexpr std::uint64_t kIchHcrBase = arch::gicv3::kIchHcrEn | arch::gicv3::kIchHcrTc;

// One-time bring-up of the virtual CPU interface (VMCR reset + HCR.En).
inline void init() noexcept {
  arch::gicv3::virtual_interface_init();
}

// Raw ICH_VTR_EL2 — vgic caches it and derives the emulated ICC_CTLR
// view and the banked VMCR reset value (nova/arch/gicv3/vtr.hpp).
inline auto vtr() noexcept -> std::uint64_t {
  return arch::gicv3::read_vtr();
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
