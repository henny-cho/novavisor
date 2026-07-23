#pragma once

// SMMUv3 command encodings used by the serialized runtime queue.

#include <array>
#include <cstdint>

namespace nova::smmu {

using CommandEntry = std::array<std::uint64_t, 2>;

namespace command {

inline constexpr std::uint64_t kOpcodeMask                  = 0xFF;
inline constexpr std::uint64_t kCfgiSte                     = 0x03;
inline constexpr std::uint64_t kTlbiS12Vmall                = 0x28;
inline constexpr std::uint64_t kTlbiNsnhAll                 = 0x30;
inline constexpr std::uint64_t kSync                        = 0x46;
inline constexpr std::uint64_t kSidShift                    = 32;
inline constexpr std::uint64_t kVmidShift                   = 32;
inline constexpr std::uint64_t kCfgiLeaf                    = 1;
inline constexpr std::uint64_t kSyncInnerShareableWriteBack = 0x0FC0'0000;

} // namespace command

[[nodiscard]] constexpr auto make_cfgi_ste(std::uint32_t stream_id) noexcept -> CommandEntry {
  return {command::kCfgiSte | (static_cast<std::uint64_t>(stream_id) << command::kSidShift), command::kCfgiLeaf};
}

[[nodiscard]] constexpr auto make_tlbi_s12_vmall(std::uint16_t vmid) noexcept -> CommandEntry {
  return {command::kTlbiS12Vmall | (static_cast<std::uint64_t>(vmid) << command::kVmidShift), 0};
}

[[nodiscard]] constexpr auto make_tlbi_nsnh_all() noexcept -> CommandEntry {
  return {command::kTlbiNsnhAll, 0};
}

[[nodiscard]] constexpr auto make_command_sync() noexcept -> CommandEntry {
  return {command::kSync | command::kSyncInnerShareableWriteBack, 0};
}

[[nodiscard]] constexpr auto command_opcode(const CommandEntry& entry) noexcept -> std::uint8_t {
  return static_cast<std::uint8_t>(entry[0] & command::kOpcodeMask);
}

} // namespace nova::smmu
