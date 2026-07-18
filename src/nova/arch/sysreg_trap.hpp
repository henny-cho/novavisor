#pragma once

// nova/arch/sysreg_trap.hpp
//
// Trapped MSR/MRS syndrome decode (EC 0x18) — the pure half of the
// system-register emulation path (the dispatch lives in trap_handler).
// Split from esr.hpp for the same reason as data_abort.hpp: consumed
// only by sysreg emulation subscribers.
//
// Reference: Arm ARM D17.2.37, "ISS encoding for an exception from
// MSR, MRS, or System instruction execution in AArch64 state". The
// encoding names the register by its (Op0, Op1, CRn, CRm, Op2) tuple
// and the transfer register by Rt (31 = xzr/wzr).
//
// This header has no dependencies beyond <cstdint> and is safe to
// include in host-side GTest builds.

#include <cstdint>

namespace nova::esr {

inline constexpr std::uint64_t kSrOp0Shift = 20U;
inline constexpr std::uint64_t kSrOp0Mask  = 0x3U;
inline constexpr std::uint64_t kSrOp2Shift = 17U;
inline constexpr std::uint64_t kSrOp2Mask  = 0x7U;
inline constexpr std::uint64_t kSrOp1Shift = 14U;
inline constexpr std::uint64_t kSrOp1Mask  = 0x7U;
inline constexpr std::uint64_t kSrCrnShift = 10U;
inline constexpr std::uint64_t kSrCrnMask  = 0xFU;
inline constexpr std::uint64_t kSrRtShift  = 5U;
inline constexpr std::uint64_t kSrRtMask   = 0x1FU;
inline constexpr std::uint64_t kSrCrmShift = 1U;
inline constexpr std::uint64_t kSrCrmMask  = 0xFU;
inline constexpr std::uint64_t kSrReadBit  = 1ULL << 0U; // Direction: 1 = MRS (read)

struct SysregTrap {
  std::uint8_t op0   = 0;
  std::uint8_t op1   = 0;
  std::uint8_t crn   = 0;
  std::uint8_t crm   = 0;
  std::uint8_t op2   = 0;
  std::uint8_t rt    = 0; // kSrtZeroReg (31) = xzr/wzr
  bool         write = false;
};

[[nodiscard]] constexpr auto parse_sysreg_trap(std::uint64_t esr) noexcept -> SysregTrap {
  return SysregTrap{
      .op0   = static_cast<std::uint8_t>((esr >> kSrOp0Shift) & kSrOp0Mask),
      .op1   = static_cast<std::uint8_t>((esr >> kSrOp1Shift) & kSrOp1Mask),
      .crn   = static_cast<std::uint8_t>((esr >> kSrCrnShift) & kSrCrnMask),
      .crm   = static_cast<std::uint8_t>((esr >> kSrCrmShift) & kSrCrmMask),
      .op2   = static_cast<std::uint8_t>((esr >> kSrOp2Shift) & kSrOp2Mask),
      .rt    = static_cast<std::uint8_t>((esr >> kSrRtShift) & kSrRtMask),
      .write = (esr & kSrReadBit) == 0U,
  };
}

// ICC_SGI1R_EL1 = S3_0_C12_C11_5 — the Group 1 SGI generation register
// (trapped by ICH_HCR_EL2.TC).
[[nodiscard]] constexpr auto is_icc_sgi1r(const SysregTrap& s) noexcept -> bool {
  return s.op0 == 3 && s.op1 == 0 && s.crn == 12 && s.crm == 11 && s.op2 == 5;
}

} // namespace nova::esr
