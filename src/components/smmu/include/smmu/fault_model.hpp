#pragma once

// Expected SMMU events and recovery actions derived from the DMA policy.

#include "nova/abi/dma.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <span>

namespace nova::smmu {

enum class EventType : std::uint8_t {
  kNone             = 0x00,
  kBadStreamId      = 0x02,
  kSteFetch         = 0x03,
  kBadSte           = 0x04,
  kCdFetch          = 0x09,
  kTranslationFault = 0x10,
};

enum class DmaAccess : std::uint8_t {
  kRead,
  kWrite,
};

enum class FaultKind : std::uint8_t {
  kNone,
  kBadStreamId,
  kBadSte,
  kTranslation,
  kInvalidPolicy,
};

enum class EventClass : std::uint8_t {
  kContextDescriptor,
  kTranslationTable,
  kInput,
  kReserved,
};

using EventRecord = std::array<std::uint64_t, 4>;

inline constexpr std::uint64_t kEventTypeMask   = 0xFF;
inline constexpr std::uint64_t kEventSsidValid  = 1ULL << 11U;
inline constexpr std::uint64_t kEventSsidShift  = 12;
inline constexpr std::uint64_t kEventSsidMask   = 0xFFFFFULL << kEventSsidShift;
inline constexpr std::uint64_t kEventSidShift   = 32;
inline constexpr std::uint64_t kEventRead       = 1ULL << 35U;
inline constexpr std::uint64_t kEventStage2     = 1ULL << 39U;
inline constexpr std::uint64_t kEventClassShift = 40;
inline constexpr std::uint64_t kEventClassMask  = 0b11ULL << kEventClassShift;
inline constexpr std::uint64_t kEventClassInput = 0b10ULL << kEventClassShift;
inline constexpr std::uint64_t kEventIpaMask    = 0x000F'FFFF'FFFF'F000ULL;
inline constexpr std::uint64_t kEventFetchMask  = 0x000F'FFFF'FFFF'FFF8ULL;

[[nodiscard]] constexpr auto make_event_header(EventType type, std::uint32_t stream_id) noexcept -> EventRecord {
  EventRecord event{};
  event[0] = static_cast<std::uint64_t>(type) | (static_cast<std::uint64_t>(stream_id) << kEventSidShift);
  return event;
}

[[nodiscard]] constexpr auto make_translation_event(std::uint32_t stream_id, std::uint64_t address,
                                                    DmaAccess access) noexcept -> EventRecord {
  EventRecord event = make_event_header(EventType::kTranslationFault, stream_id);
  event[1]          = kEventStage2 | kEventClassInput | (access == DmaAccess::kRead ? kEventRead : 0U);
  event[2]          = address;
  return event;
}

[[nodiscard]] constexpr auto event_type(const EventRecord& event) noexcept -> EventType {
  return static_cast<EventType>(event[0] & kEventTypeMask);
}

[[nodiscard]] constexpr auto event_stream_id(const EventRecord& event) noexcept -> std::uint32_t {
  return static_cast<std::uint32_t>(event[0] >> kEventSidShift);
}

[[nodiscard]] constexpr auto event_is_read(const EventRecord& event) noexcept -> bool {
  return (event[1] & kEventRead) != 0U;
}

struct DecodedEvent {
  EventRecord   raw{};
  EventType     type              = EventType::kNone;
  std::uint32_t stream_id         = 0;
  bool          known             = false;
  bool          has_substream_id  = false;
  std::uint32_t substream_id      = 0;
  bool          has_input_address = false;
  std::uint64_t input_address     = 0;
  bool          has_ipa           = false;
  std::uint64_t ipa               = 0;
  bool          has_fetch_address = false;
  std::uint64_t fetch_address     = 0;
  bool          read              = false;
  bool          stage2            = false;
  EventClass    event_class       = EventClass::kContextDescriptor;
};

[[nodiscard]] constexpr auto decode_event(const EventRecord& raw) noexcept -> DecodedEvent {
  DecodedEvent decoded{
      .raw              = raw,
      .type             = event_type(raw),
      .stream_id        = event_stream_id(raw),
      .has_substream_id = (raw[0] & kEventSsidValid) != 0U,
      .read             = event_is_read(raw),
      .stage2           = (raw[1] & kEventStage2) != 0U,
      .event_class      = static_cast<EventClass>((raw[1] & kEventClassMask) >> kEventClassShift),
  };
  if (decoded.has_substream_id) {
    decoded.substream_id = static_cast<std::uint32_t>((raw[0] & kEventSsidMask) >> kEventSsidShift);
  }
  switch (decoded.type) {
  case EventType::kNone:
  case EventType::kBadStreamId:
  case EventType::kBadSte:
    decoded.known = true;
    break;
  case EventType::kSteFetch:
    decoded.known             = true;
    decoded.has_fetch_address = true;
    decoded.fetch_address     = raw[3] & kEventFetchMask;
    break;
  case EventType::kCdFetch:
    decoded.known             = true;
    decoded.has_input_address = true;
    decoded.input_address     = raw[2];
    break;
  case EventType::kTranslationFault:
    decoded.known             = true;
    decoded.has_input_address = true;
    decoded.input_address     = raw[2];
    decoded.has_ipa           = true;
    decoded.ipa               = raw[3] & kEventIpaMask;
    break;
  }
  return decoded;
}

struct FaultExpectation {
  FaultKind        kind   = FaultKind::kNone;
  dma::FaultAction action = dma::FaultAction::kNone;
  std::size_t      vm     = dma::kNoVm;
  EventRecord      event{};

  [[nodiscard]] constexpr auto faulted() const noexcept -> bool { return kind != FaultKind::kNone; }
  [[nodiscard]] constexpr auto has_event() const noexcept -> bool { return event_type(event) != EventType::kNone; }
};

[[nodiscard]] constexpr auto classify_request(std::span<const dma::Assignment> assignments,
                                              std::span<const GuestDescriptor> guests, dma::PolicyLimits limits,
                                              std::uint32_t stream_id, std::uint64_t iova, std::uint64_t size,
                                              DmaAccess access) noexcept -> FaultExpectation {
  if (!dma::validate_policy(assignments, guests, limits).ok()) {
    return {.kind = FaultKind::kInvalidPolicy, .action = dma::FaultAction::kHaltHypervisor};
  }

  if (limits.sid_bits < 32U && stream_id >= (std::uint32_t{1} << limits.sid_bits)) {
    return {.kind   = FaultKind::kBadStreamId,
            .action = dma::FaultAction::kBlockAndAudit,
            .event  = make_event_header(EventType::kBadStreamId, stream_id)};
  }

  const dma::AccessDecision decision = dma::decide_access(assignments, guests, stream_id, iova, size);
  switch (decision.result) {
  case dma::AccessResult::kAllow:
    return {};
  case dma::AccessResult::kUnassignedStream:
    return {.kind   = FaultKind::kBadSte,
            .action = decision.action,
            .event  = make_event_header(EventType::kBadSte, stream_id)};
  case dma::AccessResult::kOutsideGuestWindow:
    return {.kind   = FaultKind::kTranslation,
            .action = decision.action,
            .vm     = decision.vm,
            .event  = make_translation_event(stream_id, iova, access)};
  case dma::AccessResult::kInvalidPolicy:
    return {.kind = FaultKind::kInvalidPolicy, .action = dma::FaultAction::kHaltHypervisor};
  }
  return {.kind = FaultKind::kInvalidPolicy, .action = dma::FaultAction::kHaltHypervisor};
}

} // namespace nova::smmu
