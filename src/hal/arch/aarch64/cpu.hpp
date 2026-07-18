#pragma once

// hal/arch/aarch64/cpu.hpp
//
// Per-core identity and the SMC conduit — pure architecture.

#include <cstddef>
#include <cstdint>

namespace nova::arch {

// This core's index. Aff0 of MPIDR_EL1 — flat cluster topology (one
// affinity level), which holds for every supported board so far.
[[nodiscard]] inline auto cpu_id() noexcept -> std::size_t {
  std::uint64_t mpidr = 0;
  __asm__ volatile("mrs %0, mpidr_el1" : "=r"(mpidr));
  return static_cast<std::size_t>(mpidr & 0xFFU);
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
