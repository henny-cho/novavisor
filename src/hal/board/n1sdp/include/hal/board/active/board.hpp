#pragma once

// C++ view of the N1SDP memory map. board_layout.h is the single source
// (it must stay preprocessor-only for the linker script); this header
// just gives its values typed names.

#include "board_layout.h"

#include <array>
#include <cstddef>
#include <cstdint>

namespace nova::board::active {

inline constexpr std::uintptr_t kUart0Base = NOVA_BOARD_UART0_BASE;
inline constexpr std::uint32_t  kUartIntid = NOVA_BOARD_UART0_INTID;

inline constexpr std::uintptr_t kGicdBase = NOVA_BOARD_GICD_BASE;
inline constexpr std::uintptr_t kGicrBase = NOVA_BOARD_GICR_BASE;
inline constexpr std::uintptr_t kSmmuBase = NOVA_BOARD_SMMU_BASE;

// Physical MPIDR affinities, indexed by core (two dual-core clusters).
// gicv3.hpp asserts the element count against NOVA_BOARD_SMP_CPUS.
inline constexpr std::size_t kSmpCpus     = NOVA_BOARD_SMP_CPUS;
inline constexpr std::array  kCpuAffinity = {0x00000000ULL, 0x00000100ULL, 0x00010000ULL, 0x00010100ULL};

inline constexpr std::uint64_t kGuestPaBase      = NOVA_BOARD_GUEST_PA_BASE;
inline constexpr std::uint64_t kGuestPaSize      = NOVA_BOARD_GUEST_PA_SIZE;
inline constexpr std::uint64_t kIvcShmPa         = NOVA_BOARD_IVC_SHM_PA;
inline constexpr std::uint64_t kGuestPristinePa  = NOVA_BOARD_PRISTINE_PA;
inline constexpr std::uint64_t kPristineSize     = NOVA_BOARD_PRISTINE_SIZE;
inline constexpr std::uint32_t kSmmuEventIntid   = NOVA_BOARD_SMMU_EVENT_INTID;
inline constexpr std::uint32_t kSmmuCommandIntid = NOVA_BOARD_SMMU_CMD_INTID;
inline constexpr std::uint32_t kSmmuErrorIntid   = NOVA_BOARD_SMMU_ERROR_INTID;

} // namespace nova::board::active
