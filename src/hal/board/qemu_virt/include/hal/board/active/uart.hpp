#pragma once

#include "board.hpp"
#include "hal/board/common/pl011.hpp"

namespace nova::board::active {

using Uart = common::Pl011<kUart0Base>;

} // namespace nova::board::active
