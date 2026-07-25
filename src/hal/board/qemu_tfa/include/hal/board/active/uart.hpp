#pragma once

// qemu_tfa shares the QEMU virt machine; only board_layout.h differs.
// Forward to the qemu_virt facade — it pulls the layout back through
// the search path, which resolves to this board's include dir.

#include "../../../../../qemu_virt/include/hal/board/active/uart.hpp"
