#pragma once

// hal/diag.hpp
//
// EL2 state snapshot facade. Two consumers: the trap_handler fatal dump,
// which needs it to attribute a failure, and the boot identity line,
// which reports the same registers so a first failure can be compared
// against the state the image actually started in.

#include "hal/arch/aarch64/exception/diag.hpp"

namespace nova::diag {

using El2State = arch::diag::El2State;

[[nodiscard]] inline auto snapshot() noexcept -> El2State {
  return arch::diag::snapshot();
}

} // namespace nova::diag
