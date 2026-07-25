#pragma once

// nova/arch/esr.hpp
//
// ESR_EL2 (Exception Syndrome Register, EL2) parsing utilities.
//
// Reference: Arm Architecture Reference Manual, D17.2.37
//
// ESR_EL2 field layout (64 bits, upper 32 bits are reserved):
//   [63:32]  RES0
//   [31:26]  EC  — Exception Class (6 bits): identifies the exception type
//   [25]     IL  — Instruction Length: 1 = 32-bit, 0 = 16-bit (Thumb)
//   [24:0]   ISS — Instruction-Specific Syndrome (25 bits)
//
// This header has no dependencies beyond <cstdint> and is safe to include
// in host-side GTest builds.

#include <cstdint>

namespace nova::esr {

// Exception Class (EC) values — ESR_EL2 bits 31:26.
// Only the classes relevant to a Type-1 AArch64 hypervisor are listed.
enum class ExceptionClass : std::uint8_t {
  kUnknown          = 0x00, // Unknown reason
  kWfx              = 0x01, // WFI/WFE instruction trapped
  kFpSimd           = 0x07, // FP/SIMD access trapped (CPTR_EL2.TFP)
  kSvcAa64          = 0x15, // SVC from AArch64 EL1
  kHvcAa64          = 0x16, // HVC from AArch64 EL1  ← primary gate for guests
  kSmcAa64          = 0x17, // SMC from AArch64 EL1
  kMsrMrs           = 0x18, // MSR/MRS/System instruction trapped
  kSve              = 0x19, // SVE instruction trapped
  kInstAbortLower   = 0x20, // Instruction Abort from lower EL (EL1/EL0)
  kInstAbortCurrent = 0x21, // Instruction Abort from current EL (EL2)
  kPcAlign          = 0x22, // PC Alignment Fault
  kDataAbortLower   = 0x24, // Data Abort from lower EL  ← MMIO trap path
  kDataAbortCurrent = 0x25, // Data Abort from current EL
  kSpAlign          = 0x26, // SP Alignment Fault
  kSerror           = 0x2F, // SError Interrupt
  kBrkptLower       = 0x30, // Breakpoint from lower EL
  kBrkptCurrent     = 0x31, // Breakpoint from current EL
  kSoftstepLower    = 0x32, // Software Step from lower EL
  kSoftstepCurrent  = 0x33, // Software Step from current EL
  kWatchptLower     = 0x34, // Watchpoint from lower EL
  kWatchptCurrent   = 0x35, // Watchpoint from current EL
  kBrk              = 0x3C, // BRK instruction
};

// A synchronous exception taken through the lower-EL vector belongs to
// the guest unless its EC claims that it originated at EL2, or reports
// an asynchronous machine error. Routed classes are handled before
// this policy is consulted.
[[nodiscard]] constexpr auto is_lower_sync_guest_fault(ExceptionClass ec) noexcept -> bool {
  switch (ec) {
  case ExceptionClass::kInstAbortCurrent:
  case ExceptionClass::kDataAbortCurrent:
  case ExceptionClass::kBrkptCurrent:
  case ExceptionClass::kSoftstepCurrent:
  case ExceptionClass::kWatchptCurrent:
  case ExceptionClass::kSerror:
    return false;
  default:
    return true;
  }
}

// Field positions and masks (see the layout table above).
inline constexpr std::uint64_t kEcShift    = 26U;
inline constexpr std::uint64_t kEcMask     = 0x3FU;
inline constexpr std::uint64_t kIlShift    = 25U;
inline constexpr std::uint64_t kIssMask    = 0x01FF'FFFFU;
inline constexpr std::uint64_t kHvcImmMask = 0xFFFFU;
inline constexpr std::uint64_t kWfxTiWfe   = 1ULL << 0U; // WFx ISS.TI bit 0: 0 = WFI, 1 = WFE

// The zero-register encoding shared by every trapped-transfer-register
// field (data-abort SRT and MSR/MRS Rt alike): reads discard, writes
// read as zero.
inline constexpr std::uint32_t kSrtZeroReg = 31U;

// Extract the Exception Class from ESR_EL2.
[[nodiscard]] inline auto get_ec(std::uint64_t esr) noexcept -> ExceptionClass {
  return static_cast<ExceptionClass>((esr >> kEcShift) & kEcMask);
}

// Extract the Instruction-Specific Syndrome (bits 24:0).
[[nodiscard]] inline auto get_iss(std::uint64_t esr) noexcept -> std::uint32_t {
  return static_cast<std::uint32_t>(esr & kIssMask);
}

// Extract the HVC/SVC immediate operand (ISS bits 15:0).
// Valid only when EC == HVC_AA64 or SVC_AA64.
[[nodiscard]] inline auto get_hvc_imm(std::uint64_t esr) noexcept -> std::uint16_t {
  return static_cast<std::uint16_t>(esr & kHvcImmMask);
}

// Extract the Instruction Length bit (ISS bit 25).
// Returns true for a 32-bit instruction, false for a 16-bit (Thumb) instruction.
[[nodiscard]] inline auto is_32bit_instruction(std::uint64_t esr) noexcept -> bool {
  return ((esr >> kIlShift) & 1U) != 0U;
}

} // namespace nova::esr
