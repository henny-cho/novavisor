#pragma once

#include <cstdint>

namespace nova::board::qemu_virt {

// Basic memory map for QEMU virt
constexpr uintptr_t UART0_BASE = 0x09000000;

// GICv3 (requires -machine virt,gic-version=3).
constexpr uintptr_t GICD_BASE = 0x08000000; // distributor
constexpr uintptr_t GICR_BASE = 0x080A0000; // redistributor frame, CPU 0

} // namespace nova::board::qemu_virt
