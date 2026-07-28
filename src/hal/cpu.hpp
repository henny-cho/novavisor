#pragma once

// hal/cpu.hpp
//
// Physical-CPU facade: core identity and the core count the board is
// built for. Components size their per-CPU state with kMaxCpus and key
// it by id() — never by touching MPIDR or board headers directly.

#include "hal/arch/aarch64/cpu.hpp"
#include "hal/board/active/board.hpp"

#include <cstddef>
#include <cstdint>

namespace nova::cpu {

inline constexpr std::size_t kMaxCpus = board::active::kSmpCpus;

// Dense core index. Seeded into TPIDR_EL2 by the boot path (boot.S),
// so this is one MRS — id() sits on every trap and scheduler path.
[[nodiscard]] inline auto id() noexcept -> std::size_t {
  return arch::core_index();
}

// Raw ID_AA64MMFR0_EL1 for the boot-time CPU contract gate.
[[nodiscard]] inline auto id_aa64mmfr0() noexcept -> std::uint64_t {
  return arch::id_aa64mmfr0();
}

// Speculation-barrier features, decoded once by the boot contract gate.
// The boot report and the guest-facing SMCCC workaround answers both
// read this, so they cannot disagree about what the PE reported.
inline void adopt_speculation_state() noexcept {
  arch::adopt_speculation_state();
}

[[nodiscard]] inline auto speculation() noexcept -> const arch::SpeculationState& {
  return arch::speculation();
}

// MPIDR affinity of a dense core index — what PSCI CPU_ON and GIC
// routing take (the two representations coincide only on flat
// single-cluster topologies).
[[nodiscard]] inline auto affinity_of(std::size_t index) noexcept -> std::uint64_t {
  return index < board::active::kCpuAffinity.size() ? board::active::kCpuAffinity[index] : 0U;
}

// The MPIDR a guest reads at EL1 — per-vCPU, installed on switch-in.
inline void write_vmpidr(std::uint64_t vcpu) noexcept {
  arch::write_vmpidr(vcpu);
}

// The hypervisor's outbound firmware conduit (SMCCC over SMC). The
// guest-facing PSCI emulation uses HVC and does not come through here.
[[nodiscard]] inline auto smc_call(std::uint64_t fid, std::uint64_t a1, std::uint64_t a2, std::uint64_t a3) noexcept
    -> std::uint64_t {
  return arch::smc_call(fid, a1, a2, a3);
}

} // namespace nova::cpu
