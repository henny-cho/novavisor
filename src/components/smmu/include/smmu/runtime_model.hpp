#pragma once

// Pure capability and memory-structure encoding for the runtime driver.

#include "nova/arch/smmuv3_regs.hpp"

#include <cstdint>
#include <limits>
#include <string_view>

namespace nova::smmu {

namespace regs = arch::smmuv3;

struct Capabilities {
  bool         stage2          = false;
  bool         aarch64_tables  = false;
  bool         coherent_access = false;
  bool         vmid16          = false;
  bool         queues_preset   = false;
  bool         tables_preset   = false;
  bool         granule4k       = false;
  std::uint8_t sid_bits        = 0;
  std::uint8_t cmdq_max_log2   = 0;
  std::uint8_t eventq_max_log2 = 0;
  std::uint8_t output_size     = 0;
};

enum class RuntimeError : std::uint8_t {
  kNone,
  kMissingStage2,
  kMissingAarch64,
  kNonCoherent,
  kPresetStructures,
  kMissingGranule4k,
  kInsufficientOutputSize,
  kInsufficientSidBits,
  kInsufficientQueues,
  kInvalidAddress,
  kInvalidAlignment,
};

[[nodiscard]] constexpr auto runtime_error_name(RuntimeError error) noexcept -> std::string_view {
  switch (error) {
  case RuntimeError::kNone:
    return "none";
  case RuntimeError::kMissingStage2:
    return "missing-stage2";
  case RuntimeError::kMissingAarch64:
    return "missing-aarch64";
  case RuntimeError::kNonCoherent:
    return "non-coherent";
  case RuntimeError::kPresetStructures:
    return "preset-structures";
  case RuntimeError::kMissingGranule4k:
    return "missing-4k-granule";
  case RuntimeError::kInsufficientOutputSize:
    return "insufficient-output-size";
  case RuntimeError::kInsufficientSidBits:
    return "insufficient-sid-bits";
  case RuntimeError::kInsufficientQueues:
    return "insufficient-queues";
  case RuntimeError::kInvalidAddress:
    return "invalid-address";
  case RuntimeError::kInvalidAlignment:
    return "invalid-alignment";
  }
  return "unknown";
}

struct RuntimeLayout {
  std::uint64_t stream_table_pa  = 0;
  std::uint64_t command_queue_pa = 0;
  std::uint64_t event_queue_pa   = 0;
  std::uint8_t  sid_bits         = 0;
  std::uint8_t  command_log2     = 0;
  std::uint8_t  event_log2       = 0;
};

inline constexpr std::uint32_t kCr1Cacheable = 0x0D75;
inline constexpr std::uint32_t kCr2Protected = regs::kCr2Protected | regs::kCr2RecordSid;
inline constexpr std::uint32_t kFaultIrqs    = regs::kIrqEvent | regs::kIrqGerror;
inline constexpr std::uint32_t kEnabledCr0   = regs::kCr0CmdqEnable | regs::kCr0EvtqEnable | regs::kCr0SmmuEnable;

[[nodiscard]] constexpr auto decode_capabilities(std::uint32_t idr0, std::uint32_t idr1, std::uint32_t idr5) noexcept
    -> Capabilities {
  return {
      .stage2          = (idr0 & regs::kIdr0S2p) != 0U,
      .aarch64_tables  = (idr0 & regs::kIdr0TtfMask) >= regs::kIdr0TtfAarch64,
      .coherent_access = (idr0 & regs::kIdr0Coherent) != 0U,
      .vmid16          = (idr0 & regs::kIdr0Vmid16) != 0U,
      .queues_preset   = (idr1 & regs::kIdr1QueuePreset) != 0U,
      .tables_preset   = (idr1 & regs::kIdr1TablePreset) != 0U,
      .granule4k       = (idr5 & regs::kIdr5Granule4k) != 0U,
      .sid_bits        = static_cast<std::uint8_t>(idr1 & regs::kIdr1SidSizeMask),
      .cmdq_max_log2   = static_cast<std::uint8_t>((idr1 >> regs::kIdr1CmdqsShift) & regs::kIdr1QsizeMask),
      .eventq_max_log2 = static_cast<std::uint8_t>((idr1 >> regs::kIdr1EvtqsShift) & regs::kIdr1QsizeMask),
      .output_size     = static_cast<std::uint8_t>(idr5 & regs::kIdr5OasMask),
  };
}

[[nodiscard]] constexpr auto validate_capabilities(const Capabilities& caps, const RuntimeLayout& layout) noexcept
    -> RuntimeError {
  if (!caps.stage2) {
    return RuntimeError::kMissingStage2;
  }
  if (!caps.aarch64_tables) {
    return RuntimeError::kMissingAarch64;
  }
  if (!caps.coherent_access) {
    return RuntimeError::kNonCoherent;
  }
  if (caps.queues_preset || caps.tables_preset) {
    return RuntimeError::kPresetStructures;
  }
  if (!caps.granule4k) {
    return RuntimeError::kMissingGranule4k;
  }
  if (caps.output_size < 2U) {
    return RuntimeError::kInsufficientOutputSize;
  }
  if (layout.sid_bits == 0U || layout.sid_bits >= std::numeric_limits<std::uint32_t>::digits ||
      caps.sid_bits < layout.sid_bits) {
    return RuntimeError::kInsufficientSidBits;
  }
  if (layout.command_log2 == 0U || layout.event_log2 == 0U || caps.cmdq_max_log2 < layout.command_log2 ||
      caps.eventq_max_log2 < layout.event_log2) {
    return RuntimeError::kInsufficientQueues;
  }
  if ((layout.stream_table_pa & ~regs::kPhysicalMask) != 0U || (layout.command_queue_pa & ~regs::kPhysicalMask) != 0U ||
      (layout.event_queue_pa & ~regs::kPhysicalMask) != 0U) {
    return RuntimeError::kInvalidAddress;
  }

  const std::uint64_t table_alignment   = std::uint64_t{1} << (layout.sid_bits + 6U);
  const std::uint64_t command_alignment = std::uint64_t{1} << (layout.command_log2 + 4U);
  const std::uint64_t event_alignment   = std::uint64_t{1} << (layout.event_log2 + 5U);
  if ((layout.stream_table_pa & (table_alignment - 1U)) != 0U ||
      (layout.command_queue_pa & (command_alignment - 1U)) != 0U ||
      (layout.event_queue_pa & (event_alignment - 1U)) != 0U) {
    return RuntimeError::kInvalidAlignment;
  }
  return RuntimeError::kNone;
}

[[nodiscard]] constexpr auto stream_table_base(std::uint64_t pa) noexcept -> std::uint64_t {
  return (pa & regs::kStrtabAddrMask) | regs::kStrtabBaseRa;
}

[[nodiscard]] constexpr auto queue_base(std::uint64_t pa, std::uint8_t log2_entries) noexcept -> std::uint64_t {
  return (pa & regs::kQueueAddrMask) | regs::kQueueBaseRwa | log2_entries;
}

[[nodiscard]] constexpr auto stream_table_config(std::uint8_t sid_bits) noexcept -> std::uint32_t {
  return sid_bits;
}

} // namespace nova::smmu
