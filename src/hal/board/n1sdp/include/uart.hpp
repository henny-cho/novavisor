#pragma once

#include "board.hpp"
#include "hal/board/common/pl011.hpp"

namespace nova::board::n1sdp {

using Uart = common::Pl011<UART0_BASE>;

} // namespace nova::board::n1sdp
