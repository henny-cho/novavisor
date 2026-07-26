#pragma once

// hal/timer.hpp
//
// Generic-timer facade; the arch tree is selected at build time. The
// hypervisor owns the EL2 physical timer, guests keep the virtual one.

#include "hal/arch/aarch64/timer.hpp"

namespace nova {

namespace hyp_timer = arch::hyp_timer;

} // namespace nova
