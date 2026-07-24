#pragma once

// hal/cpu.hpp
//
// Physical-CPU facade: core identity and the core count the board is
// built for. Components size their per-CPU state with kMaxCpus and key
// it by id() — never by touching MPIDR or board headers directly.

#include "hal/arch/aarch64/cpu.hpp"
#include "hal/board/active/board.hpp"

#include <cstddef>

namespace nova::cpu {

inline constexpr std::size_t kMaxCpus = board::active::kSmpCpus;

[[nodiscard]] inline auto id() noexcept -> std::size_t {
  return arch::cpu_id();
}

} // namespace nova::cpu
