#pragma once

// nova/arch/gicv3/vtr.hpp
//
// ICH_VTR_EL2-derived interface facts and the two register views that
// must track them (Arm IHI 0069 §9.4.19): the ICC_CTLR_EL1 value the
// vGIC emulates for guests, and the ICH_VMCR_EL2 reset value whose
// binary points must not fall below the implemented minimum. Pure and
// host-testable; the raw ICH_VTR value comes in through the hal facade.

#include <cstddef>
#include <cstdint>

namespace nova::arch::gicv3 {

inline constexpr std::uint64_t kVmcrVeng1   = 1ULL << 1;     // virtual Group 1 enable
inline constexpr std::uint64_t kVmcrVpmrAll = 0xFFULL << 24; // guest PMR: accept all

// ICH_VTR_EL2 fields. PRIbits and PREbits are minus-one encoded, the
// same encoding their ICC_CTLR_EL1 counterparts use.
[[nodiscard]] constexpr auto vtr_list_regs(std::uint64_t vtr) noexcept -> std::size_t {
  return static_cast<std::size_t>(vtr & 0x1FU) + 1;
}

[[nodiscard]] constexpr auto vtr_pri_bits_field(std::uint64_t vtr) noexcept -> std::uint64_t {
  return (vtr >> 29U) & 0x7U;
}

[[nodiscard]] constexpr auto vtr_pre_bits_field(std::uint64_t vtr) noexcept -> std::uint64_t {
  return (vtr >> 26U) & 0x7U;
}

[[nodiscard]] constexpr auto vtr_id_bits_field(std::uint64_t vtr) noexcept -> std::uint64_t {
  return (vtr >> 23U) & 0x7U;
}

// Emulated ICC_CTLR_EL1 read value: mirror the implemented PRIbits
// [10:8] and IDbits [13:11]; EOImode and CBPR stay 0. A fabricated
// zero would tell the guest "one priority bit, 16-bit INTIDs".
[[nodiscard]] constexpr auto icc_ctlr_view(std::uint64_t vtr) noexcept -> std::uint64_t {
  return (vtr_id_bits_field(vtr) << 11U) | (vtr_pri_bits_field(vtr) << 8U);
}

// ICH_VMCR_EL2 reset: guest PMR accepts all, Group 1 enabled, and both
// binary points at the implemented minimum — VBPR0 below 7 − PREbits
// is not honoured, and VBPR1's floor sits one above VBPR0's.
[[nodiscard]] constexpr auto vmcr_reset(std::uint64_t vtr) noexcept -> std::uint64_t {
  const std::uint64_t vbpr0 = 7U - vtr_pre_bits_field(vtr);
  const std::uint64_t vbpr1 = vbpr0 >= 7U ? 7U : vbpr0 + 1U;
  return kVmcrVpmrAll | kVmcrVeng1 | (vbpr0 << 21U) | (vbpr1 << 18U);
}

} // namespace nova::arch::gicv3
