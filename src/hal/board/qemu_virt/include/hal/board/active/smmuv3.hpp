#pragma once

#include "board.hpp"
#include "hal/board/common/smmuv3.hpp"

namespace nova::board::active {

using Smmuv3 = common::Smmuv3<kSmmuBase>;

} // namespace nova::board::active
