#pragma once

// C++ view of the QEMU virt memory map. board_layout.h is the single
// source (it must stay preprocessor-only for the linker script); this
// header just gives its values typed names.

#include "board_layout.h"

#include <cstddef>
#include <cstdint>

namespace nova::board::qemu_virt {

constexpr uintptr_t            UART0_BASE = NOVA_BOARD_UART0_BASE;
inline constexpr std::uint32_t kUartIntid = NOVA_BOARD_UART0_INTID;

// GICv3 (requires -machine virt,gic-version=3).
constexpr uintptr_t GICD_BASE = NOVA_BOARD_GICD_BASE; // distributor
constexpr uintptr_t GICR_BASE = NOVA_BOARD_GICR_BASE; // redistributor frame, CPU 0

constexpr uintptr_t SMMU_BASE = NOVA_BOARD_SMMU_BASE;
constexpr uintptr_t SMMU_SIZE = NOVA_BOARD_SMMU_SIZE;

inline constexpr std::size_t   kSmpCpus          = NOVA_BOARD_SMP_CPUS;
inline constexpr std::uint32_t kSmmuEventIntid   = NOVA_BOARD_SMMU_EVENT_INTID;
inline constexpr std::uint32_t kSmmuCommandIntid = NOVA_BOARD_SMMU_CMD_INTID;
inline constexpr std::uint32_t kSmmuErrorIntid   = NOVA_BOARD_SMMU_ERROR_INTID;

} // namespace nova::board::qemu_virt
