#pragma once

// C++ view of the QEMU virt memory map. board_layout.h is the single
// source (it must stay preprocessor-only for the linker script); this
// header just gives its values typed names.

#include "board_layout.h"

#include <cstdint>

namespace nova::board::qemu_virt {

constexpr uintptr_t UART0_BASE = NOVA_BOARD_UART0_BASE;

// GICv3 (requires -machine virt,gic-version=3).
constexpr uintptr_t GICD_BASE = NOVA_BOARD_GICD_BASE; // distributor
constexpr uintptr_t GICR_BASE = NOVA_BOARD_GICR_BASE; // redistributor frame, CPU 0

constexpr uintptr_t SMMU_BASE = NOVA_BOARD_SMMU_BASE;
constexpr uintptr_t SMMU_SIZE = NOVA_BOARD_SMMU_SIZE;

} // namespace nova::board::qemu_virt
