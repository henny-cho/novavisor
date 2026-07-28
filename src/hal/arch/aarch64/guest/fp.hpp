#pragma once

// hal/arch/aarch64/guest/fp.hpp
//
// FP/SIMD register bank + CPTR_EL2.TFP control — the mechanism behind
// lazy guest FP switching. The hardware FP register file belongs to at
// most one VCPU at a time; everyone else runs with the trap set and
// claims the file on first use. Ownership policy lives in core_vcpu;
// this header only moves the registers and flips the trap.
//
// EL2 itself is compiled -mgeneral-regs-only and post-link verified
// FP-free (novakit/image/fp_free.sh), so the trap can stay set while
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

// Whole-register CPTR_EL2 value (E2H = 0): the RES1 bits plus TZ so an
// SVE-capable part traps guest SVE (no SVE bank exists; TZ is RES1
// when SVE is absent). Written wholesale — TCPAC/TAM/TTA left set by
// firmware would otherwise trap guest CPACR/AMU/trace accesses that
// have no handler.
inline constexpr std::uint64_t kCptrEl2Res1 = 0x32FFULL;
inline constexpr std::uint64_t kCptrTz      = 1ULL << 8U;
inline constexpr std::uint64_t kCptrEl2Base = kCptrEl2Res1 | kCptrTz;

// Set/clear the EL0/EL1 FP-access trap (EC 0x07 → EL2). The ISB makes
// the change take effect immediately — required before the bank swap
// when clearing, ordered anyway before the ERET when setting.
inline void set_fp_trap(bool trap) noexcept {
  const std::uint64_t v = kCptrEl2Base | (trap ? kCptrTfp : 0U);
  __asm__ volatile("msr cptr_el2, %0\n\tisb" ::"r"(v));
}

// Same, without the trailing ISB — for the guest switch-in path only,
// where the following ERET is context-synchronizing. Never use this
// when EL2 code touches FP right afterwards (bank swaps need the
// synchronous set_fp_trap).
inline void set_fp_trap_before_eret(bool trap) noexcept {
  const std::uint64_t v = kCptrEl2Base | (trap ? kCptrTfp : 0U);
  __asm__ volatile("msr cptr_el2, %0" ::"r"(v));
}

} // namespace nova::arch

// hal/arch/aarch64/guest/fp.S — the only code in the EL2 image allowed to
// touch FP registers. CPTR_EL2.TFP must be clear when these run.
extern "C" void nova_fp_save(nova::arch::FpBank* bank) noexcept;
extern "C" void nova_fp_restore(const nova::arch::FpBank* bank) noexcept;
