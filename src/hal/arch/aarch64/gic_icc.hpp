#pragma once

// hal/arch/aarch64/gic_icc.hpp
//
// GICv3 physical CPU interface (ICC_* system registers) — pure
// architecture, no board dependency. System registers are written with
// their S3_* encodings so the assembler needs no GIC architecture
// extension; the architectural name is given next to each access.

#include <cstdint>

namespace nova::arch::gicv3 {

inline constexpr std::uint64_t kIccSreSre     = 1ULL << 0; // system-register interface
inline constexpr std::uint64_t kIccSreEnable  = 1ULL << 3; // allow lower-EL ICC_SRE access
inline constexpr std::uint64_t kPmrAcceptAll  = 0xFF;      // lowest priority mask
inline constexpr std::uint64_t kIgrpen1Enable = 1ULL << 0;

inline void cpu_interface_init() noexcept {
  std::uint64_t v = kIccSreSre | kIccSreEnable;
  __asm__ volatile("msr S3_4_C12_C9_5, %0" ::"r"(v)); // ICC_SRE_EL2
  __asm__ volatile("isb");
  v = kIccSreSre;
  __asm__ volatile("msr S3_0_C12_C12_5, %0" ::"r"(v)); // ICC_SRE_EL1
  __asm__ volatile("isb");
  v = kPmrAcceptAll;
  __asm__ volatile("msr S3_0_C4_C6_0, %0" ::"r"(v)); // ICC_PMR_EL1
  v = kIgrpen1Enable;
  __asm__ volatile("msr S3_0_C12_C12_7, %0" ::"r"(v)); // ICC_IGRPEN1_EL1
  __asm__ volatile("isb");
}

// Acknowledge the highest-priority pending Group 1 interrupt.
inline auto ack() noexcept -> std::uint32_t {
  std::uint64_t v = 0;
  __asm__ volatile("mrs %0, S3_0_C12_C12_0" : "=r"(v)); // ICC_IAR1_EL1
  return static_cast<std::uint32_t>(v);
}

// Priority-drop + deactivate (ICC_CTLR_EL1.EOImode stays 0).
inline void eoi(std::uint32_t intid) noexcept {
  const auto v = static_cast<std::uint64_t>(intid);
  __asm__ volatile("msr S3_0_C12_C12_1, %0" ::"r"(v)); // ICC_EOIR1_EL1
}

} // namespace nova::arch::gicv3
