#pragma once

// Board-selected guest DMA device.

#include "hal/board/qemu_virt/include/pci_edu.hpp"

namespace nova::dma_device::hw {

namespace device = board::qemu_virt::pci_edu;

} // namespace nova::dma_device::hw
