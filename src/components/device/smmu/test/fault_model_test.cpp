#include "smmu/fault_model.hpp"

#include <gtest/gtest.h>

namespace {

using nova::smmu::decode_event;
using nova::smmu::EventRecord;
using nova::smmu::EventType;

// The words the hardware writes: type and stream ID in word 0, the
// addresses the record carries in words 2 and 3.
constexpr auto record(EventType type, std::uint32_t stream_id = 0) noexcept -> EventRecord {
  return {static_cast<std::uint64_t>(type) | (std::uint64_t{stream_id} << nova::smmu::kEventSidShift), 0, 0, 0};
}

TEST(SmmuFault, DecoderKeepsZeroAddressPresenceAndRawRecord) {
  EventRecord raw    = record(EventType::kTranslationFault, 0x10);
  raw[3]             = 0x1234'5000;
  const auto decoded = decode_event(raw);

  EXPECT_TRUE(decoded.known);
  EXPECT_EQ(decoded.stream_id, 0x10);
  EXPECT_TRUE(decoded.has_input_address);
  EXPECT_EQ(decoded.input_address, 0); // present and zero, not absent
  EXPECT_TRUE(decoded.has_ipa);
  EXPECT_EQ(decoded.ipa, 0x1234'5000);
  EXPECT_EQ(decoded.raw, raw);
}

TEST(SmmuFault, DecoderUsesSsvToGateSubstreamId) {
  EventRecord raw = record(EventType::kBadSte, 0x10);
  raw[0] |= 0xABCDEULL << nova::smmu::kEventSsidShift;
  auto decoded = decode_event(raw);
  EXPECT_FALSE(decoded.has_substream_id);
  EXPECT_EQ(decoded.substream_id, 0);

  raw[0] |= nova::smmu::kEventSsidValid;
  decoded = decode_event(raw);
  EXPECT_TRUE(decoded.has_substream_id);
  EXPECT_EQ(decoded.substream_id, 0xABCDE);
}

TEST(SmmuFault, DecoderAppliesEventSpecificAddressSemantics) {
  EventRecord raw = record(EventType::kSteFetch);
  raw[3]          = 0x1234'567F;
  auto decoded    = decode_event(raw);
  EXPECT_TRUE(decoded.has_fetch_address);
  EXPECT_EQ(decoded.fetch_address, 0x1234'5678); // the low control bits are not address
  EXPECT_FALSE(decoded.has_input_address);

  raw     = record(EventType::kCdFetch);
  decoded = decode_event(raw);
  EXPECT_TRUE(decoded.has_input_address);
  EXPECT_EQ(decoded.input_address, 0);
  EXPECT_FALSE(decoded.has_fetch_address);
}

TEST(SmmuFault, DecoderPreservesUnknownEvents) {
  const EventRecord raw{{0x55, 0x1111, 0x2222, 0x3333}};
  const auto        decoded = decode_event(raw);
  EXPECT_FALSE(decoded.known);
  EXPECT_EQ(decoded.raw, raw);
  EXPECT_EQ(static_cast<std::uint8_t>(decoded.type), 0x55);
}

TEST(SmmuFault, ClassifiesOwnerIsolationEvents) {
  for (const EventType type : {EventType::kTranslationFault, EventType::kAddressSizeFault, EventType::kAccessFault,
                               EventType::kPermissionFault}) {
    EXPECT_TRUE(nova::smmu::requires_quarantine(decode_event(record(type, 0x10))));
  }
  EXPECT_FALSE(nova::smmu::requires_quarantine(decode_event(record(EventType::kBadSte, 0x10))));
}

} // namespace
