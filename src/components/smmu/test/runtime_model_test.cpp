#include "smmu/runtime_model.hpp"

#include <gtest/gtest.h>

namespace {

using namespace nova::smmu;

constexpr std::uint32_t kQemuIdr0 = 0x0D44'101B;
constexpr std::uint32_t kQemuIdr1 = 0x0273'0010;
constexpr std::uint32_t kQemuIdr5 = 0x0000'0074;

constexpr RuntimeLayout kLayout{
    .stream_table_pa  = 0x4000'0000,
    .command_queue_pa = 0x4000'1000,
    .event_queue_pa   = 0x4000'2000,
    .sid_bits         = 5,
    .command_log2     = 4,
    .event_log2       = 4,
};

TEST(SmmuRuntime, DecodesQemuCapabilities) {
  constexpr Capabilities caps = decode_capabilities(kQemuIdr0, kQemuIdr1, kQemuIdr5);

  EXPECT_TRUE(caps.stage2);
  EXPECT_TRUE(caps.aarch64_tables);
  EXPECT_TRUE(caps.coherent_access);
  EXPECT_TRUE(caps.vmid16);
  EXPECT_FALSE(caps.queues_preset);
  EXPECT_FALSE(caps.tables_preset);
  EXPECT_TRUE(caps.granule4k);
  EXPECT_EQ(caps.sid_bits, 16);
  EXPECT_EQ(caps.cmdq_max_log2, 19);
  EXPECT_EQ(caps.eventq_max_log2, 19);
  EXPECT_EQ(caps.output_size, 4);
  EXPECT_EQ(validate_capabilities(caps, kLayout), RuntimeError::kNone);
}

TEST(SmmuRuntime, RejectsMissingTranslationFeatures) {
  constexpr Capabilities qemu = decode_capabilities(kQemuIdr0, kQemuIdr1, kQemuIdr5);

  auto caps   = qemu;
  caps.stage2 = false;
  EXPECT_EQ(validate_capabilities(caps, kLayout), RuntimeError::kMissingStage2);

  caps                = qemu;
  caps.aarch64_tables = false;
  EXPECT_EQ(validate_capabilities(caps, kLayout), RuntimeError::kMissingAarch64);

  caps                 = qemu;
  caps.coherent_access = false;
  EXPECT_EQ(validate_capabilities(caps, kLayout), RuntimeError::kNonCoherent);

  caps           = qemu;
  caps.granule4k = false;
  EXPECT_EQ(validate_capabilities(caps, kLayout), RuntimeError::kMissingGranule4k);

  caps             = qemu;
  caps.output_size = 1;
  EXPECT_EQ(validate_capabilities(caps, kLayout), RuntimeError::kInsufficientOutputSize);
}

TEST(SmmuRuntime, NamesCapabilityFailures) {
  EXPECT_EQ(runtime_error_name(RuntimeError::kNone), "none");
  EXPECT_EQ(runtime_error_name(RuntimeError::kMissingStage2), "missing-stage2");
  EXPECT_EQ(runtime_error_name(RuntimeError::kMissingGranule4k), "missing-4k-granule");
  EXPECT_EQ(runtime_error_name(RuntimeError::kInsufficientQueues), "insufficient-queues");
  EXPECT_EQ(runtime_error_name(static_cast<RuntimeError>(UINT8_MAX)), "unknown");
}

TEST(SmmuRuntime, RejectsUnsupportedSoftwareStructures) {
  constexpr Capabilities qemu = decode_capabilities(kQemuIdr0, kQemuIdr1, kQemuIdr5);

  auto caps          = qemu;
  caps.queues_preset = true;
  EXPECT_EQ(validate_capabilities(caps, kLayout), RuntimeError::kPresetStructures);

  caps          = qemu;
  caps.sid_bits = 4;
  EXPECT_EQ(validate_capabilities(caps, kLayout), RuntimeError::kInsufficientSidBits);

  caps               = qemu;
  caps.cmdq_max_log2 = 3;
  EXPECT_EQ(validate_capabilities(caps, kLayout), RuntimeError::kInsufficientQueues);
}

TEST(SmmuRuntime, RejectsInvalidRuntimeMemory) {
  constexpr Capabilities caps = decode_capabilities(kQemuIdr0, kQemuIdr1, kQemuIdr5);

  auto layout            = kLayout;
  layout.stream_table_pa = 1ULL << 52U;
  EXPECT_EQ(validate_capabilities(caps, layout), RuntimeError::kInvalidAddress);

  layout = kLayout;
  layout.stream_table_pa += 64;
  EXPECT_EQ(validate_capabilities(caps, layout), RuntimeError::kInvalidAlignment);

  layout = kLayout;
  layout.command_queue_pa += 32;
  EXPECT_EQ(validate_capabilities(caps, layout), RuntimeError::kInvalidAlignment);

  layout = kLayout;
  layout.event_queue_pa += 256;
  EXPECT_EQ(validate_capabilities(caps, layout), RuntimeError::kInvalidAlignment);
}

TEST(SmmuRuntime, EncodesBasesAndControlProfile) {
  EXPECT_EQ(stream_table_base(kLayout.stream_table_pa), 0x4000'0000'4000'0000);
  EXPECT_EQ(queue_base(kLayout.command_queue_pa, kLayout.command_log2), 0x4000'0000'4000'1004);
  EXPECT_EQ(stream_table_config(kLayout.sid_bits), 5);
  EXPECT_EQ(kCr1Cacheable, 0x0D75);
  EXPECT_EQ(kCr2Protected, 0x6);
  EXPECT_EQ(kFaultIrqs, 0x5);
  EXPECT_EQ(kEnabledCr0, 0xD);
}

} // namespace
