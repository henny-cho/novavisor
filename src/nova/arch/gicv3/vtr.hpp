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

// Both derived views, against the interfaces the supported parts
// report. The ICH_VTR_EL2 encoder here is the layout named above:
// ListRegs [4:0], IDbits [25:23], PREbits [28:26], PRIbits [31:29].
static_assert(
    [] {
      const auto vtr = [](std::uint64_t list_regs, std::uint64_t id_bits, std::uint64_t pre_bits,
                          std::uint64_t pri_bits) {
        return list_regs | (id_bits << 23U) | (pre_bits << 26U) | (pri_bits << 29U);
      };
      return vtr_list_regs(vtr(3, 0, 6, 7)) == 4 &&   // the field is one less than the count
             vtr_list_regs(vtr(15, 0, 6, 7)) == 16 && // the widest interface the register can describe
             // 8 priority bits (field 7) and 24-bit INTIDs (field 1), placed at PRIbits [10:8], IDbits [13:11].
             icc_ctlr_view(vtr(3, 1, 6, 7)) == ((1ULL << 11U) | (7ULL << 8U)) &&
             icc_ctlr_view(vtr(3, 0, 4, 4)) == (4ULL << 8U) && // 5 priority bits, 16-bit INTIDs
             // 7 preemption bits (field 6): VBPR0 floors at 1, VBPR1 one above it.
             vmcr_reset(vtr(3, 0, 6, 7)) == (kVmcrVpmrAll | kVmcrVeng1 | (1ULL << 21U) | (2ULL << 18U)) &&
             // 8 preemption bits: VBPR0 reaches zero, VBPR1 still cannot.
             vmcr_reset(vtr(3, 0, 7, 7)) == (kVmcrVpmrAll | kVmcrVeng1 | (1ULL << 18U)) &&
             // 5 preemption bits: both floors rise with the narrower interface.
             vmcr_reset(vtr(3, 0, 4, 4)) == (kVmcrVpmrAll | kVmcrVeng1 | (3ULL << 21U) | (4ULL << 18U));
    }(),
    "the emulated interface reports what the hardware implements, never less");

} // namespace nova::arch::gicv3
