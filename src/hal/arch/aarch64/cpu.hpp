#pragma once

// hal/arch/aarch64/cpu.hpp
//
// Per-core identity and the SMC conduit — pure architecture.

#include "nova/arch/cpu_features.hpp"
#include "nova/arch/mpidr.h"

#include <cstddef>
#include <cstdint>

namespace nova::arch {

// Physical affinity in the MPIDR/IROUTER representation. Board code
// maps this value to its dense per-CPU state index.
[[nodiscard]] inline auto cpu_affinity() noexcept -> std::uint64_t {
  std::uint64_t mpidr = 0;
  __asm__ volatile("mrs %0, mpidr_el1" : "=r"(mpidr));
  return mpidr & std::uint64_t{NOVA_MPIDR_AFFINITY_MASK};
}

// Dense per-core index, seeded by the boot path (boot.S writes each
// core's kCpuAffinity position into TPIDR_EL2 before any C++ runs —
// the register is otherwise unused at EL2). One MRS replaces the MPIDR
// mask + affinity-table scan on every hot-path cpu::id() call.
[[nodiscard]] inline auto core_index() noexcept -> std::size_t {
  std::uint64_t index = 0;
  __asm__ volatile("mrs %0, tpidr_el2" : "=r"(index));
  return static_cast<std::size_t>(index);
}

// Raw ID_AA64MMFR0_EL1 for the boot-time CPU contract gate
// (nova/arch/cpu_contract.hpp decodes and validates it).
[[nodiscard]] inline auto id_aa64mmfr0() noexcept -> std::uint64_t {
  std::uint64_t v = 0;
  __asm__ volatile("mrs %0, id_aa64mmfr0_el1" : "=r"(v));
  return v;
}

// The speculation-barrier ID registers, decoded by
// nova/arch/cpu_features.hpp into the verdict the boot report and the
// guest-facing SMCCC answers share.
[[nodiscard]] inline auto id_aa64pfr0() noexcept -> std::uint64_t {
  std::uint64_t v = 0;
  __asm__ volatile("mrs %0, id_aa64pfr0_el1" : "=r"(v));
  return v;
}

[[nodiscard]] inline auto id_aa64pfr1() noexcept -> std::uint64_t {
  std::uint64_t v = 0;
  __asm__ volatile("mrs %0, id_aa64pfr1_el1" : "=r"(v));
  return v;
}

[[nodiscard]] inline auto id_aa64isar2() noexcept -> std::uint64_t {
  std::uint64_t v = 0;
  __asm__ volatile("mrs %0, id_aa64isar2_el1" : "=r"(v));
  return v;
}

namespace detail {
// Decoded once by the boot contract gate. Two independent consumers read
// it — the boot report and the guest-facing SMCCC workaround answers —
// and they must agree, so the decode happens in one place. Same handoff
// premise as the adopted timebase: written before secondaries exist.
inline SpeculationState g_speculation{};
} // namespace detail

inline void adopt_speculation_state() noexcept {
  detail::g_speculation = read_speculation_state(id_aa64pfr0(), id_aa64pfr1(), id_aa64isar2());
}

[[nodiscard]] inline auto speculation() noexcept -> const SpeculationState& {
  return detail::g_speculation;
}

// The MPIDR value the guest reads at EL1. Per-vCPU: written on every
// switch-in so a guest identifies its vCPU by Aff0, independent of the
// physical core underneath. Bit 31 is RES1; U (bit 30) stays 0 — the
// guest sees an SMP-capable topology. The following ERET synchronizes.
inline void write_vmpidr(std::uint64_t vcpu) noexcept {
  const std::uint64_t v = (1ULL << 31) | (vcpu & 0xFFU);
  __asm__ volatile("msr vmpidr_el2, %0" ::"r"(v));
}

// SMCCC call toward firmware (SMC conduit). The guest-facing PSCI
// emulation uses HVC; this is the hypervisor's own outbound side —
// on QEMU virt with EL2 enabled the machine intercepts SMC as PSCI.
// SMCCC allows the callee to clobber x4-x17.
[[nodiscard]] inline auto smc_call(std::uint64_t fid, std::uint64_t a1, std::uint64_t a2, std::uint64_t a3) noexcept
    -> std::uint64_t {
  register std::uint64_t x0 __asm__("x0") = fid;
  register std::uint64_t x1 __asm__("x1") = a1;
  register std::uint64_t x2 __asm__("x2") = a2;
  register std::uint64_t x3 __asm__("x3") = a3;
  __asm__ volatile("smc #0"
                   : "+r"(x0), "+r"(x1), "+r"(x2), "+r"(x3)
                   :
                   : "x4", "x5", "x6", "x7", "x8", "x9", "x10", "x11", "x12", "x13", "x14", "x15", "x16", "x17",
                     "memory");
  return x0;
}

} // namespace nova::arch
