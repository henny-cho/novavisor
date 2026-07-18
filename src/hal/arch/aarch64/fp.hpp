#pragma once

// hal/arch/aarch64/fp.hpp
//
// FP/SIMD register bank + CPTR_EL2.TFP control — the mechanism behind
// lazy guest FP switching. The hardware FP register file belongs to at
// most one VCPU at a time; everyone else runs with the trap set and
// claims the file on first use. Ownership policy lives in core_vcpu;
// this header only moves the registers and flips the trap.
//
// EL2 itself is compiled -mgeneral-regs-only and post-link verified
// FP-free (scripts/check_fp_free.sh), so the trap can stay set while
// the hypervisor runs without risk of a self-trap.

#include <array>
#include <cstdint>

namespace nova::arch {

// Full FP/SIMD state: Q0–Q31 (128 bits each) plus the status/control
// pair. Layout is fixed — fp.S addresses it by immediate offsets.
struct alignas(16) FpBank {
  std::array<std::uint64_t, 64> q{}; // Q0–Q31
  std::uint64_t                 fpsr = 0;
  std::uint64_t                 fpcr = 0;
};

static_assert(sizeof(FpBank) == 528, "fp.S offsets depend on this layout");

inline constexpr std::uint64_t kCptrTfp = 1ULL << 10U; // TFP (E2H = 0)

// Set/clear the EL0/EL1 FP-access trap (EC 0x07 → EL2). Read-modify-
// write keeps the RES1 bits the hardware reset to. The ISB makes the
// change take effect immediately — required before the bank swap when
// clearing, ordered anyway before the ERET when setting.
inline void set_fp_trap(bool trap) noexcept {
  std::uint64_t v = 0;
  __asm__ volatile("mrs %0, cptr_el2" : "=r"(v));
  v = trap ? (v | kCptrTfp) : (v & ~kCptrTfp);
  __asm__ volatile("msr cptr_el2, %0\n\tisb" ::"r"(v));
}

} // namespace nova::arch

// hal/arch/aarch64/fp.S — the only code in the EL2 image allowed to
// touch FP registers. CPTR_EL2.TFP must be clear when these run.
extern "C" void nova_fp_save(nova::arch::FpBank* bank) noexcept;
extern "C" void nova_fp_restore(const nova::arch::FpBank* bank) noexcept;
