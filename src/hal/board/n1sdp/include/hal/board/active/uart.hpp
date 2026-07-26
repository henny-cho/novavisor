#pragma once

#include "board.hpp"
#include "hal/drivers/pl011.hpp"

namespace nova::board::active {

using Uart = drivers::Pl011<kUart0Base>;

} // namespace nova::board::active
