#pragma once

// SMMU event records: what the hardware wrote, and what it means.
// Decoding is the whole job — the recovery a fault calls for is the
// DMA policy's answer, asked through dma_device, not restated here.

#include <array>
#include <cstdint>

namespace nova::smmu {

enum class EventType : std::uint8_t {
  kNone             = 0x00,
  kBadStreamId      = 0x02,
  kSteFetch         = 0x03,
  kBadSte           = 0x04,
  kCdFetch          = 0x09,
  kTranslationFault = 0x10,
  kAddressSizeFault = 0x11,
  kAccessFault      = 0x12,
  kPermissionFault  = 0x13,
};

using EventRecord = std::array<std::uint64_t, 4>;

inline constexpr std::uint64_t kEventTypeMask  = 0xFF;
inline constexpr std::uint64_t kEventSsidValid = 1ULL << 11U;
inline constexpr std::uint64_t kEventSsidShift = 12;
inline constexpr std::uint64_t kEventSsidMask  = 0xFFFFFULL << kEventSsidShift;
inline constexpr std::uint64_t kEventSidShift  = 32;
inline constexpr std::uint64_t kEventIpaMask   = 0x000F'FFFF'FFFF'F000ULL;
inline constexpr std::uint64_t kEventFetchMask = 0x000F'FFFF'FFFF'FFF8ULL;

[[nodiscard]] constexpr auto event_type(const EventRecord& event) noexcept -> EventType {
  return static_cast<EventType>(event[0] & kEventTypeMask);
}

[[nodiscard]] constexpr auto event_stream_id(const EventRecord& event) noexcept -> std::uint32_t {
  return static_cast<std::uint32_t>(event[0] >> kEventSidShift);
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
};

[[nodiscard]] constexpr auto decode_event(const EventRecord& raw) noexcept -> DecodedEvent {
  DecodedEvent decoded{
      .raw              = raw,
      .type             = event_type(raw),
      .stream_id        = event_stream_id(raw),
      .has_substream_id = (raw[0] & kEventSsidValid) != 0U,
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
  case EventType::kAddressSizeFault:
  case EventType::kAccessFault:
  case EventType::kPermissionFault:
    decoded.known             = true;
    decoded.has_input_address = true;
    decoded.input_address     = raw[2];
    decoded.has_ipa           = true;
    decoded.ipa               = raw[3] & kEventIpaMask;
    break;
  }
  return decoded;
}

[[nodiscard]] constexpr auto requires_quarantine(const DecodedEvent& event) noexcept -> bool {
  switch (event.type) {
  case EventType::kTranslationFault:
  case EventType::kAddressSizeFault:
  case EventType::kAccessFault:
  case EventType::kPermissionFault:
    return true;
  case EventType::kNone:
  case EventType::kBadStreamId:
  case EventType::kSteFetch:
  case EventType::kBadSte:
  case EventType::kCdFetch:
    return false;
  }
  return false;
}

} // namespace nova::smmu
