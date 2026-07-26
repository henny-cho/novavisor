#pragma once

// hal/diag.hpp
//
// Fatal-dump state facade — consumed by the trap_handler dump only.

#include "hal/arch/aarch64/exception/diag.hpp"

namespace nova::diag {

using El2State = arch::diag::El2State;

[[nodiscard]] inline auto snapshot() noexcept -> El2State {
  return arch::diag::snapshot();
}

} // namespace nova::diag
