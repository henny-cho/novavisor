#pragma once

// novavisor/esr.hpp
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

namespace novavisor::esr {

// Exception Class (EC) values — ESR_EL2 bits 31:26.
// Only the classes relevant to a Type-1 AArch64 hypervisor are listed.
enum class ExceptionClass : std::uint8_t {
  UNKNOWN            = 0x00, // Unknown reason
  WFx                = 0x01, // WFI/WFE instruction trapped
  SVC_AA64           = 0x15, // SVC from AArch64 EL1
  HVC_AA64           = 0x16, // HVC from AArch64 EL1  ← primary gate for guests
  SMC_AA64           = 0x17, // SMC from AArch64 EL1
  MSR_MRS            = 0x18, // MSR/MRS/System instruction trapped
  SVE                = 0x19, // SVE instruction trapped
  INST_ABORT_LOWER   = 0x20, // Instruction Abort from lower EL (EL1/EL0)
  INST_ABORT_CURRENT = 0x21, // Instruction Abort from current EL (EL2)
  PC_ALIGN           = 0x22, // PC Alignment Fault
  DATA_ABORT_LOWER   = 0x24, // Data Abort from lower EL  ← MMIO trap path
  DATA_ABORT_CURRENT = 0x25, // Data Abort from current EL
  SP_ALIGN           = 0x26, // SP Alignment Fault
  SERROR             = 0x2F, // SError Interrupt
  BRKPT_LOWER        = 0x30, // Breakpoint from lower EL
  BRKPT_CURRENT      = 0x31, // Breakpoint from current EL
  SOFTSTEP_LOWER     = 0x32, // Software Step from lower EL
  WATCHPT_LOWER      = 0x34, // Watchpoint from lower EL
  BRK                = 0x3C, // BRK instruction
};

// Extract the Exception Class from ESR_EL2.
[[nodiscard]] inline auto get_ec(std::uint64_t esr) noexcept -> ExceptionClass {
  return static_cast<ExceptionClass>((esr >> 26U) & 0x3FU);
}

// Extract the Instruction-Specific Syndrome (bits 24:0).
[[nodiscard]] inline auto get_iss(std::uint64_t esr) noexcept -> std::uint32_t {
  return static_cast<std::uint32_t>(esr & 0x01FF'FFFFU);
}

// Extract the HVC/SVC immediate operand (ISS bits 15:0).
// Valid only when EC == HVC_AA64 or SVC_AA64.
[[nodiscard]] inline auto get_hvc_imm(std::uint64_t esr) noexcept -> std::uint16_t {
  return static_cast<std::uint16_t>(esr & 0xFFFFU);
}

// Extract the Instruction Length bit (ISS bit 25).
// Returns true for a 32-bit instruction, false for a 16-bit (Thumb) instruction.
[[nodiscard]] inline auto is_32bit_instruction(std::uint64_t esr) noexcept -> bool {
  return ((esr >> 25U) & 1U) != 0U;
}

} // namespace novavisor::esr
