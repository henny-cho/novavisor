#include "smmu/fault_model.hpp"

#include <array>
#include <gtest/gtest.h>

namespace {

using nova::GuestDescriptor;
using nova::dma::Assignment;
using nova::dma::FaultAction;
using nova::smmu::DmaAccess;
using nova::smmu::EventType;
using nova::smmu::FaultKind;

constexpr std::array<GuestDescriptor, 1> kGuests{{
    {.ipa_base = 0x5000'0000, .ipa_size = 0x0080'0000, .load_pa = 0x5080'0000},
}};
constexpr std::array<Assignment, 1>      kAssignments{{{.stream_id = 0x10, .vm = 0}}};
constexpr nova::dma::PolicyLimits        kLimits{.sid_bits = 8};

TEST(SmmuFault, AllowsOwnedAddressWithoutEvent) {
  const auto result =
      nova::smmu::classify_request(kAssignments, kGuests, kLimits, 0x10, 0x5000'1000, 8, DmaAccess::kRead);
  EXPECT_FALSE(result.faulted());
  EXPECT_FALSE(result.has_event());
}

TEST(SmmuFault, OutOfRangeSidProducesBadStreamRecord) {
  const auto result =
      nova::smmu::classify_request(kAssignments, kGuests, kLimits, 0x100, 0x5000'0000, 8, DmaAccess::kWrite);
  ASSERT_EQ(result.kind, FaultKind::kBadStreamId);
  EXPECT_EQ(result.action, FaultAction::kBlockAndAudit);
  EXPECT_EQ(nova::smmu::event_type(result.event), EventType::kBadStreamId);
  EXPECT_EQ(nova::smmu::event_stream_id(result.event), 0x100);
  EXPECT_FALSE(nova::smmu::decode_event(result.event).has_input_address);
}

TEST(SmmuFault, UnassignedSidProducesBadSteRecord) {
  const auto result =
      nova::smmu::classify_request(kAssignments, kGuests, kLimits, 0x20, 0x5000'0000, 8, DmaAccess::kWrite);
  ASSERT_EQ(result.kind, FaultKind::kBadSte);
  EXPECT_EQ(result.action, FaultAction::kBlockAndAudit);
  EXPECT_EQ(nova::smmu::event_type(result.event), EventType::kBadSte);
  EXPECT_EQ(nova::smmu::event_stream_id(result.event), 0x20);
}

TEST(SmmuFault, InvalidAddressProducesStage2TranslationRecord) {
  const auto result =
      nova::smmu::classify_request(kAssignments, kGuests, kLimits, 0x10, 0x6000'0000, 8, DmaAccess::kRead);
  ASSERT_EQ(result.kind, FaultKind::kTranslation);
  EXPECT_EQ(result.action, FaultAction::kQuarantineAndResetVm);
  EXPECT_EQ(result.vm, 0);
  EXPECT_EQ(nova::smmu::event_type(result.event), EventType::kTranslationFault);
  EXPECT_EQ(nova::smmu::event_stream_id(result.event), 0x10);
  EXPECT_EQ(nova::smmu::decode_event(result.event).input_address, 0x6000'0000);
  EXPECT_TRUE(nova::smmu::event_is_read(result.event));
  EXPECT_NE(result.event[1] & nova::smmu::kEventStage2, 0);
  EXPECT_EQ(result.event[1] & nova::smmu::kEventClassMask, nova::smmu::kEventClassInput);
}

TEST(SmmuFault, InvalidPolicyHaltsWithoutHardwareEvent) {
  constexpr std::array<Assignment, 1> invalid{{{.stream_id = 0x10, .vm = 1}}};
  const auto result = nova::smmu::classify_request(invalid, kGuests, kLimits, 0x10, 0x5000'0000, 8, DmaAccess::kWrite);
  EXPECT_EQ(result.kind, FaultKind::kInvalidPolicy);
  EXPECT_EQ(result.action, FaultAction::kHaltHypervisor);
  EXPECT_FALSE(result.has_event());
}

TEST(SmmuFault, DecoderKeepsZeroAddressPresenceAndRawRecord) {
  auto raw           = nova::smmu::make_translation_event(0x10, 0, DmaAccess::kWrite);
  raw[3]             = 0x1234'5000;
  const auto decoded = nova::smmu::decode_event(raw);

  EXPECT_TRUE(decoded.known);
  EXPECT_TRUE(decoded.has_input_address);
  EXPECT_EQ(decoded.input_address, 0);
  EXPECT_TRUE(decoded.has_ipa);
  EXPECT_EQ(decoded.ipa, 0x1234'5000);
  EXPECT_FALSE(decoded.read);
  EXPECT_TRUE(decoded.stage2);
  EXPECT_EQ(decoded.event_class, nova::smmu::EventClass::kInput);
  EXPECT_EQ(decoded.raw, raw);
}

TEST(SmmuFault, DecoderUsesSsvToGateSubstreamId) {
  nova::smmu::EventRecord raw{};
  raw[0] = static_cast<std::uint64_t>(EventType::kBadSte) | (0xABCDEULL << nova::smmu::kEventSsidShift) |
           (0x10ULL << nova::smmu::kEventSidShift);
  auto decoded = nova::smmu::decode_event(raw);
  EXPECT_FALSE(decoded.has_substream_id);
  EXPECT_EQ(decoded.substream_id, 0);

  raw[0] |= nova::smmu::kEventSsidValid;
  decoded = nova::smmu::decode_event(raw);
  EXPECT_TRUE(decoded.has_substream_id);
  EXPECT_EQ(decoded.substream_id, 0xABCDE);
}

TEST(SmmuFault, DecoderAppliesEventSpecificAddressSemantics) {
  nova::smmu::EventRecord raw{};
  raw[0]       = static_cast<std::uint64_t>(EventType::kSteFetch);
  raw[3]       = 0x1234'567F;
  auto decoded = nova::smmu::decode_event(raw);
  EXPECT_TRUE(decoded.has_fetch_address);
  EXPECT_EQ(decoded.fetch_address, 0x1234'5678);
  EXPECT_FALSE(decoded.has_input_address);

  raw[0]  = static_cast<std::uint64_t>(EventType::kCdFetch);
  raw[2]  = 0;
  decoded = nova::smmu::decode_event(raw);
  EXPECT_TRUE(decoded.has_input_address);
  EXPECT_EQ(decoded.input_address, 0);
  EXPECT_FALSE(decoded.has_fetch_address);
}

TEST(SmmuFault, DecoderPreservesUnknownEvents) {
  const nova::smmu::EventRecord raw{{0x55, 0x1111, 0x2222, 0x3333}};
  const auto                    decoded = nova::smmu::decode_event(raw);
  EXPECT_FALSE(decoded.known);
  EXPECT_EQ(decoded.raw, raw);
  EXPECT_EQ(static_cast<std::uint8_t>(decoded.type), 0x55);
}

TEST(SmmuFault, ClassifiesOwnerIsolationEvents) {
  for (const EventType type : {EventType::kTranslationFault, EventType::kAddressSizeFault, EventType::kAccessFault,
                               EventType::kPermissionFault}) {
    auto raw = nova::smmu::make_event_header(type, 0x10);
    EXPECT_TRUE(nova::smmu::requires_quarantine(nova::smmu::decode_event(raw)));
  }
  EXPECT_FALSE(nova::smmu::requires_quarantine(
      nova::smmu::decode_event(nova::smmu::make_event_header(EventType::kBadSte, 0x10))));
}

} // namespace
