#pragma once

#include "board_layout.h"

#include <array>
#include <cstddef>
#include <cstdint>

namespace nova::board::n1sdp {

inline constexpr std::uintptr_t UART0_BASE = NOVA_BOARD_UART0_BASE;
inline constexpr std::uint32_t  kUartIntid = NOVA_BOARD_UART0_INTID;

inline constexpr std::uintptr_t GICD_BASE = NOVA_BOARD_GICD_BASE;
inline constexpr std::uintptr_t GICR_BASE = NOVA_BOARD_GICR_BASE;
inline constexpr std::uintptr_t SMMU_BASE = NOVA_BOARD_SMMU_BASE;
inline constexpr std::uintptr_t SMMU_SIZE = NOVA_BOARD_SMMU_SIZE;

inline constexpr std::size_t                         kSmpCpus = NOVA_BOARD_SMP_CPUS;
inline constexpr std::array<std::uint64_t, kSmpCpus> kCpuAffinity{
    0x00000000,
    0x00000100,
    0x00010000,
    0x00010100,
};
inline constexpr std::uint64_t kGuestPaBase      = NOVA_BOARD_GUEST_PA_BASE;
inline constexpr std::uint64_t kGuestPaSize      = NOVA_BOARD_GUEST_PA_SIZE;
inline constexpr std::uint64_t kIvcShmPa         = NOVA_BOARD_IVC_SHM_PA;
inline constexpr std::uint64_t kGuestPristinePa  = NOVA_BOARD_PRISTINE_PA;
inline constexpr std::uint64_t kPristineSize     = NOVA_BOARD_PRISTINE_SIZE;
inline constexpr std::uint32_t kSmmuEventIntid   = NOVA_BOARD_SMMU_EVENT_INTID;
inline constexpr std::uint32_t kSmmuCommandIntid = NOVA_BOARD_SMMU_CMD_INTID;
inline constexpr std::uint32_t kSmmuErrorIntid   = NOVA_BOARD_SMMU_ERROR_INTID;

} // namespace nova::board::n1sdp
