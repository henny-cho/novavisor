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

// A record as the device writes it, spelled in literal words. The field
// positions are the architecture's, so they are pinned against literals
// rather than against the constants the decoder reads them with: a shift
// that moved would still decode self-consistently, and the quarantine it
// triggers would isolate a VM that never issued the transaction.
static_assert(
    [] {
      const DecodedEvent fault =
          decode_event(EventRecord{0x0000'0025'0000'0710ULL, 0, 0x1234'5678'9ABC'DEF0ULL, 0x00FA'BCDE'F123'4FFFULL});
      const DecodedEvent wide = decode_event(EventRecord{0xFFFF'FFFF'0000'0002ULL, 0, 0, 0});
      return fault.type == EventType::kTranslationFault && // bits 7:0, nothing above them
             fault.stream_id == 0x25U &&                   // bits 63:32
             wide.stream_id == 0xFFFF'FFFFU &&             // the whole StreamID, no bit lost
             wide.type == EventType::kBadStreamId && requires_quarantine(fault) && !requires_quarantine(wide) &&
             fault.has_input_address && fault.input_address == 0x1234'5678'9ABC'DEF0ULL && // word 2, whole
             fault.has_ipa && fault.ipa == 0x000A'BCDE'F123'4000ULL &&                     // word 3, bits 51:12
             !fault.has_fetch_address && !fault.has_substream_id;                          // bit 11 was clear
    }(),
    "a fault names the StreamID and the IPA from the words and widths the architecture puts them in");

// SSV sits below the field it gates. A record whose SubstreamID bits are
// set but whose valid bit is not names no context, and reading it anyway
// would attribute the fault to a stream the device never used.
static_assert(
    [] {
      const DecodedEvent gated = decode_event(EventRecord{0x0000'0025'ABCD'E004ULL, 0, 0, 0}); // bit 11 clear
      const DecodedEvent named = decode_event(EventRecord{0x0000'0025'ABCD'E804ULL, 0, 0, 0}); // bit 11 set
      return !gated.has_substream_id && gated.substream_id == 0U && named.has_substream_id &&
             named.substream_id == 0xABCDEU &&                               // bits 31:12
             named.type == EventType::kBadSte && gated.type == named.type && // the valid bit is not part of the type
             named.stream_id == 0x25U;
    }(),
    "the SubstreamID is read from bits 31:12, and only when bit 11 says it was written");

// Which word carries an address, and how much of it, is per event type.
// An STE fetch reports where the walk went with the low control bits
// dropped; a CD fetch reports the address the device asked for, whole.
// Neither is the guest's doing, so neither quarantines it.
static_assert(
    [] {
      const DecodedEvent ste = decode_event(EventRecord{0x0000'0025'0000'0003ULL, 0, 0, 0x00FA'BCDE'F123'4FFFULL});
      const DecodedEvent cd  = decode_event(EventRecord{0x0000'0025'0000'0009ULL, 0, 0x0000'0000'DEAD'BEEFULL, 0});
      return ste.type == EventType::kSteFetch && ste.has_fetch_address &&
             ste.fetch_address == 0x000A'BCDE'F123'4FF8ULL &&          // word 3, bits 51:3
             !ste.has_input_address && !ste.has_ipa &&                 //
             cd.type == EventType::kCdFetch && cd.has_input_address && //
             cd.input_address == 0xDEAD'BEEFULL &&                     // word 2, whole
             !cd.has_fetch_address && !cd.has_ipa &&                   //
             !requires_quarantine(ste) && !requires_quarantine(cd);
    }(),
    "each fetch failure reports its address from the word and with the width its event type gives it");

} // namespace nova::smmu
