#pragma once

#include "board.hpp"
#include "hal/drivers/smmuv3.hpp"

namespace nova::board::active {

using Smmuv3 = drivers::Smmuv3<kSmmuBase>;

} // namespace nova::board::active
