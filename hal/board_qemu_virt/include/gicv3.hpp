#pragma once

// GICv3 driver for the QEMU virt board (-machine gic-version=3).
//
// Register offsets and bit layouts are architectural (Arm IHI 0069,
// GICv3/v4 Architecture Specification); only GICD_BASE / GICR_BASE come
// from the board memory map. Single-PE bring-up only — Phase 9 (SMP)
// generalizes the per-CPU redistributor lookup.
//
// System registers are written with their S3_* encodings so the
// assembler needs no GIC architecture extension; the architectural name
// is given next to each access.

#include "board.hpp"

#include <cstdint>

namespace nova::board::qemu_virt::gicv3 {

// --- Distributor (GICD_BASE + offset) ---
inline constexpr uintptr_t kGicdCtlr           = 0x0;
inline constexpr uint32_t  kGicdCtlrEnableGrp1 = 1U << 1; // Group 1 forwarding
inline constexpr uint32_t  kGicdCtlrAre        = 1U << 4; // affinity routing

// --- Redistributor, RD_base frame (GICR_BASE + offset) ---
inline constexpr uintptr_t kGicrWaker               = 0x14;
inline constexpr uint32_t  kGicrWakerProcessorSleep = 1U << 1;
inline constexpr uint32_t  kGicrWakerChildrenAsleep = 1U << 2;

// --- Redistributor, SGI_base frame (RD_base + 64 KiB + offset) ---
inline constexpr uintptr_t kGicrSgiFrame   = 0x10000;
inline constexpr uintptr_t kGicrIgroupr0   = kGicrSgiFrame + 0x80;  // SGI/PPI group select
inline constexpr uintptr_t kGicrIsenabler0 = kGicrSgiFrame + 0x100; // SGI/PPI enable set

inline auto mmio32(uintptr_t addr) noexcept -> volatile uint32_t* {
  return reinterpret_cast<volatile uint32_t*>(addr);
}

// Enable Group 1 forwarding at the distributor, wake the CPU 0
// redistributor, and put every SGI/PPI in Group 1 (the group the CPU
// interface enables below).
inline void distributor_init() noexcept {
  *mmio32(GICD_BASE + kGicdCtlr) = kGicdCtlrAre;
  *mmio32(GICD_BASE + kGicdCtlr) = kGicdCtlrAre | kGicdCtlrEnableGrp1;

  auto* const waker = mmio32(GICR_BASE + kGicrWaker);
  *waker            = *waker & ~kGicrWakerProcessorSleep;
  while ((*waker & kGicrWakerChildrenAsleep) != 0U) {
    // wait for the redistributor to wake
  }

  *mmio32(GICR_BASE + kGicrIgroupr0) = ~0U;
}

// Enable one SGI/PPI (INTID 0..31) at the redistributor.
inline void enable_ppi(uint32_t intid) noexcept {
  *mmio32(GICR_BASE + kGicrIsenabler0) = 1U << intid;
}

// --- Physical CPU interface (ICC_*, system registers) ---

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
inline auto ack() noexcept -> uint32_t {
  std::uint64_t v = 0;
  __asm__ volatile("mrs %0, S3_0_C12_C12_0" : "=r"(v)); // ICC_IAR1_EL1
  return static_cast<uint32_t>(v);
}

// Priority-drop + deactivate (ICC_CTLR_EL1.EOImode stays 0).
inline void eoi(uint32_t intid) noexcept {
  const auto v = static_cast<std::uint64_t>(intid);
  __asm__ volatile("msr S3_0_C12_C12_1, %0" ::"r"(v)); // ICC_EOIR1_EL1
}

// --- EL2 virtual CPU interface (ICH_*) ---

inline constexpr std::uint64_t kIchVmcrVeng1   = 1ULL << 1;     // virtual Group 1 enable
inline constexpr std::uint64_t kIchVmcrVpmrAll = 0xFFULL << 24; // guest PMR: accept all
inline constexpr std::uint64_t kIchHcrEn       = 1ULL << 0;

inline void virtual_interface_init() noexcept {
  std::uint64_t v = kIchVmcrVpmrAll | kIchVmcrVeng1;
  __asm__ volatile("msr S3_4_C12_C11_7, %0" ::"r"(v)); // ICH_VMCR_EL2
  v = kIchHcrEn;
  __asm__ volatile("msr S3_4_C12_C11_0, %0" ::"r"(v)); // ICH_HCR_EL2
  __asm__ volatile("isb");
}

// Program / read back List Register 0. At most one vIRQ is in flight
// per VCPU (the scheduler shadows this register across VM switches);
// a full vGIC allocates LRs dynamically (Phase 8).
inline void write_lr0(std::uint64_t value) noexcept {
  __asm__ volatile("msr S3_4_C12_C12_0, %0" ::"r"(value)); // ICH_LR0_EL2
  __asm__ volatile("isb");
}

inline auto read_lr0() noexcept -> std::uint64_t {
  std::uint64_t v = 0;
  __asm__ volatile("mrs %0, S3_4_C12_C12_0" : "=r"(v)); // ICH_LR0_EL2
  return v;
}

} // namespace nova::board::qemu_virt::gicv3
