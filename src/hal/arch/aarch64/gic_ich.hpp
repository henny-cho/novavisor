#pragma once

// hal/arch/aarch64/gic_ich.hpp
//
// GICv3 EL2 virtual CPU interface (ICH_* system registers) — pure
// architecture, no board dependency. This is the hardware half of
// virtual interrupt injection; LR bit encoding and injection policy
// live in the vgic component's pure model.

#include <cstddef>
#include <cstdint>

namespace nova::arch::gicv3 {

inline constexpr std::uint64_t kIchVmcrVeng1   = 1ULL << 1;     // virtual Group 1 enable
inline constexpr std::uint64_t kIchVmcrVpmrAll = 0xFFULL << 24; // guest PMR: accept all
inline constexpr std::uint64_t kIchHcrEn       = 1ULL << 0;
inline constexpr std::uint64_t kIchHcrUie      = 1ULL << 1;  // underflow maintenance IRQ
inline constexpr std::uint64_t kIchHcrTc       = 1ULL << 10; // trap ICC_SGI*R/DIR (common regs)
inline constexpr std::uint64_t kIchVtrListMask = 0x1FULL;    // ListRegs = count - 1

inline void virtual_interface_init() noexcept {
  std::uint64_t v = kIchVmcrVpmrAll | kIchVmcrVeng1;
  __asm__ volatile("msr S3_4_C12_C11_7, %0" ::"r"(v)); // ICH_VMCR_EL2
  v = kIchHcrEn | kIchHcrTc;
  __asm__ volatile("msr S3_4_C12_C11_0, %0" ::"r"(v)); // ICH_HCR_EL2
  __asm__ volatile("isb");
}

// Number of implemented list registers (ICH_VTR_EL2.ListRegs + 1).
inline auto list_register_count() noexcept -> std::size_t {
  std::uint64_t v = 0;
  __asm__ volatile("mrs %0, S3_4_C12_C11_1" : "=r"(v)); // ICH_VTR_EL2
  return static_cast<std::size_t>(v & kIchVtrListMask) + 1;
}

// ICH_VMCR_EL2 / ICH_HCR_EL2 accessors — per-VCPU state for the
// scheduler (guests mutate VMCR through their ICV_* view).
inline auto read_vmcr() noexcept -> std::uint64_t {
  std::uint64_t v = 0;
  __asm__ volatile("mrs %0, S3_4_C12_C11_7" : "=r"(v));
  return v;
}

inline void write_vmcr(std::uint64_t value) noexcept {
  __asm__ volatile("msr S3_4_C12_C11_7, %0" ::"r"(value));
}

inline auto read_hcr() noexcept -> std::uint64_t {
  std::uint64_t v = 0;
  __asm__ volatile("mrs %0, S3_4_C12_C11_0" : "=r"(v));
  return v;
}

inline void write_hcr(std::uint64_t value) noexcept {
  __asm__ volatile("msr S3_4_C12_C11_0, %0" ::"r"(value));
}

// Indexed list-register access: ICH_LR0..7 live in c12, ICH_LR8..15 in
// c13. No barrier — every path back into the guest ends in an ERET,
// which is the context synchronization event that publishes them.
// Out-of-range indices are ignored / read as zero (callers iterate up
// to list_register_count()).
// A macro (not constexpr) because each list register is a distinct
// sysreg name baked into the asm template string; `op` cannot be
// parenthesized inside an asm statement.
// NOLINTBEGIN(cppcoreguidelines-macro-usage, bugprone-macro-parentheses)
#define NOVA_GICV3_LR_CASE(n, op)                                                                                      \
  case (n):                                                                                                            \
    __asm__ volatile(op : "+r"(value));                                                                                \
    break;
// NOLINTEND(cppcoreguidelines-macro-usage, bugprone-macro-parentheses)

inline void write_lr(std::size_t index, std::uint64_t value) noexcept {
  switch (index) {
    NOVA_GICV3_LR_CASE(0, "msr S3_4_C12_C12_0, %0")
    NOVA_GICV3_LR_CASE(1, "msr S3_4_C12_C12_1, %0")
    NOVA_GICV3_LR_CASE(2, "msr S3_4_C12_C12_2, %0")
    NOVA_GICV3_LR_CASE(3, "msr S3_4_C12_C12_3, %0")
    NOVA_GICV3_LR_CASE(4, "msr S3_4_C12_C12_4, %0")
    NOVA_GICV3_LR_CASE(5, "msr S3_4_C12_C12_5, %0")
    NOVA_GICV3_LR_CASE(6, "msr S3_4_C12_C12_6, %0")
    NOVA_GICV3_LR_CASE(7, "msr S3_4_C12_C12_7, %0")
    NOVA_GICV3_LR_CASE(8, "msr S3_4_C12_C13_0, %0")
    NOVA_GICV3_LR_CASE(9, "msr S3_4_C12_C13_1, %0")
    NOVA_GICV3_LR_CASE(10, "msr S3_4_C12_C13_2, %0")
    NOVA_GICV3_LR_CASE(11, "msr S3_4_C12_C13_3, %0")
    NOVA_GICV3_LR_CASE(12, "msr S3_4_C12_C13_4, %0")
    NOVA_GICV3_LR_CASE(13, "msr S3_4_C12_C13_5, %0")
    NOVA_GICV3_LR_CASE(14, "msr S3_4_C12_C13_6, %0")
    NOVA_GICV3_LR_CASE(15, "msr S3_4_C12_C13_7, %0")
  default:
    break;
  }
}

inline auto read_lr(std::size_t index) noexcept -> std::uint64_t {
  std::uint64_t value = 0;
  switch (index) {
    NOVA_GICV3_LR_CASE(0, "mrs %0, S3_4_C12_C12_0")
    NOVA_GICV3_LR_CASE(1, "mrs %0, S3_4_C12_C12_1")
    NOVA_GICV3_LR_CASE(2, "mrs %0, S3_4_C12_C12_2")
    NOVA_GICV3_LR_CASE(3, "mrs %0, S3_4_C12_C12_3")
    NOVA_GICV3_LR_CASE(4, "mrs %0, S3_4_C12_C12_4")
    NOVA_GICV3_LR_CASE(5, "mrs %0, S3_4_C12_C12_5")
    NOVA_GICV3_LR_CASE(6, "mrs %0, S3_4_C12_C12_6")
    NOVA_GICV3_LR_CASE(7, "mrs %0, S3_4_C12_C12_7")
    NOVA_GICV3_LR_CASE(8, "mrs %0, S3_4_C12_C13_0")
    NOVA_GICV3_LR_CASE(9, "mrs %0, S3_4_C12_C13_1")
    NOVA_GICV3_LR_CASE(10, "mrs %0, S3_4_C12_C13_2")
    NOVA_GICV3_LR_CASE(11, "mrs %0, S3_4_C12_C13_3")
    NOVA_GICV3_LR_CASE(12, "mrs %0, S3_4_C12_C13_4")
    NOVA_GICV3_LR_CASE(13, "mrs %0, S3_4_C12_C13_5")
    NOVA_GICV3_LR_CASE(14, "mrs %0, S3_4_C12_C13_6")
    NOVA_GICV3_LR_CASE(15, "mrs %0, S3_4_C12_C13_7")
  default:
    break;
  }
  return value;
}

#undef NOVA_GICV3_LR_CASE

} // namespace nova::arch::gicv3
