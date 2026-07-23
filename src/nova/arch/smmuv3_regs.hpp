#pragma once

#include <cstdint>

namespace nova::arch::smmuv3 {

inline constexpr std::uint32_t kIdr0    = 0x0000;
inline constexpr std::uint32_t kIdr1    = 0x0004;
inline constexpr std::uint32_t kIdr5    = 0x0014;
inline constexpr std::uint32_t kCr0     = 0x0020;
inline constexpr std::uint32_t kCr0Ack  = 0x0024;
inline constexpr std::uint32_t kCr1     = 0x0028;
inline constexpr std::uint32_t kCr2     = 0x002C;
inline constexpr std::uint32_t kGbpa    = 0x0044;
inline constexpr std::uint32_t kIrqCtrl = 0x0050;
inline constexpr std::uint32_t kIrqAck  = 0x0054;
inline constexpr std::uint32_t kGerror  = 0x0060;
inline constexpr std::uint32_t kGerrorN = 0x0064;

inline constexpr std::uint32_t kStrtabBase    = 0x0080;
inline constexpr std::uint32_t kStrtabBaseCfg = 0x0088;
inline constexpr std::uint32_t kCmdqBase      = 0x0090;
inline constexpr std::uint32_t kCmdqProd      = 0x0098;
inline constexpr std::uint32_t kCmdqCons      = 0x009C;
inline constexpr std::uint32_t kEvtqBase      = 0x00A0;
inline constexpr std::uint32_t kEvtqProd      = 0x00A8;
inline constexpr std::uint32_t kEvtqCons      = 0x00AC;

inline constexpr std::uint32_t kIdr0S2p         = 1U << 0;
inline constexpr std::uint32_t kIdr0TtfMask     = 3U << 2;
inline constexpr std::uint32_t kIdr0TtfAarch64  = 2U << 2;
inline constexpr std::uint32_t kIdr0Coherent    = 1U << 4;
inline constexpr std::uint32_t kIdr0Vmid16      = 1U << 18;
inline constexpr std::uint32_t kIdr1SidSizeMask = 0x3FU;
inline constexpr std::uint32_t kIdr1EvtqsShift  = 16U;
inline constexpr std::uint32_t kIdr1CmdqsShift  = 21U;
inline constexpr std::uint32_t kIdr1QsizeMask   = 0x1FU;
inline constexpr std::uint32_t kIdr1QueuePreset = 1U << 29;
inline constexpr std::uint32_t kIdr1TablePreset = 1U << 30;
inline constexpr std::uint32_t kIdr5OasMask     = 0x7U;
inline constexpr std::uint32_t kIdr5Granule4k   = 1U << 4;

inline constexpr std::uint32_t kCr0SmmuEnable = 1U << 0;
inline constexpr std::uint32_t kCr0EvtqEnable = 1U << 2;
inline constexpr std::uint32_t kCr0CmdqEnable = 1U << 3;
inline constexpr std::uint32_t kCr2RecordSid  = 1U << 1;
inline constexpr std::uint32_t kCr2Protected  = 1U << 2;
inline constexpr std::uint32_t kGbpaAbort     = 1U << 20;
inline constexpr std::uint32_t kGbpaUpdate    = 1U << 31;

inline constexpr std::uint32_t kIrqGerror = 1U << 0;
inline constexpr std::uint32_t kIrqEvent  = 1U << 2;

inline constexpr std::uint64_t kStrtabBaseRa   = 1ULL << 62;
inline constexpr std::uint64_t kStrtabAddrMask = 0x000F'FFFF'FFFF'FFC0ULL;
inline constexpr std::uint64_t kQueueBaseRwa   = 1ULL << 62;
inline constexpr std::uint64_t kQueueAddrMask  = 0x000F'FFFF'FFFF'FFE0ULL;
inline constexpr std::uint64_t kPhysicalMask   = 0x000F'FFFF'FFFF'FFFFULL;

} // namespace nova::arch::smmuv3
