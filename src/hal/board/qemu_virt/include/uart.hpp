#pragma once

#include "board.hpp"
#include "hal/board/common/pl011.hpp"

namespace nova::board::qemu_virt {

using Uart = common::Pl011<UART0_BASE>;

} // namespace nova::board::qemu_virt
