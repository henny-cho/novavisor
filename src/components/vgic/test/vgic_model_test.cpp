// tests/host/vgic_model_test.cpp
//
// Host-side GTest suite for the pure vGICv3 register model
// (components/vgic/include/vgic_model.hpp): register emulation on the
// GICD/GICR frames. Delivery is covered by vgic_delivery_test.cpp.

#include "vgic/vgic_model.hpp"

#include <gtest/gtest.h>

using namespace nova::vgic;

// ---------------------------------------------------------------------------
// Reset state
// ---------------------------------------------------------------------------

TEST(VgicReset, SgisEnabledPpisDisabledAllGroup1) {
  const RedistState r{};
  EXPECT_EQ(r.isenabler0, 0xFFFFU);
  EXPECT_EQ(r.igroupr0, ~0U);
  EXPECT_EQ(r.pending, 0U);
  EXPECT_TRUE(r.asleep);
}

// ---------------------------------------------------------------------------
// Distributor frame
// ---------------------------------------------------------------------------

TEST(VgicDist, CtlrRoundTrips) {
  DistState d{};
  EXPECT_TRUE(dist_write(d, kGicdCtlr, 4, 0x12));
  const auto r = dist_read(d, kGicdCtlr, 4);
  EXPECT_TRUE(r.known);
  EXPECT_EQ(r.value, 0x12U);
}

TEST(VgicDist, TyperAdvertisesNoSpis) {
  const DistState d{};
  EXPECT_EQ(dist_read(d, kGicdTyper, 4).value, 0U);
}

TEST(VgicDist, Pidr2IsGicV3) {
  const DistState d{};
  EXPECT_EQ(dist_read(d, kGicdPidr2, 4).value, 0x30U);
}

TEST(VgicDist, UnknownOffsetReported) {
  DistState d{};
  EXPECT_FALSE(dist_read(d, 0x0080, 4).known); // GICD_IGROUPR0: RES0 under ARE
  EXPECT_FALSE(dist_write(d, 0x0100, 4, ~0U));
}

// ---------------------------------------------------------------------------
// Redistributor frame
// ---------------------------------------------------------------------------

TEST(VgicRedist, WakerHandshake) {
  RedistState c{};
  auto        r = redist_read(c, kGicrWaker, 4);
  EXPECT_EQ(r.value, kWakerProcessorSleep | kWakerChildrenAsleep);

  EXPECT_TRUE(redist_write(c, kGicrWaker, 4, 0));
  r = redist_read(c, kGicrWaker, 4);
  EXPECT_EQ(r.value, 0U); // ChildrenAsleep clears with ProcessorSleep
}

TEST(VgicRedist, TyperReportsLast) {
  const RedistState c{};
  // Default identity: frame 0 of a single-vCPU VM — Last set, number 0.
  EXPECT_EQ(redist_read(c, kGicrTyper, 8).value, static_cast<std::uint64_t>(kGicrTyperLast));
  EXPECT_EQ(redist_read(c, kGicrTyperHi, 4).value, 0U);
}

TEST(VgicRedist, TyperEncodesFrameIdentity) {
  const RedistState          c{};
  const nova::vgic::RedistId frame0{.number = 0, .last = false};
  const nova::vgic::RedistId frame1{.number = 1, .last = true};

  // Frame 0 of a 2-vCPU VM: Processor_Number 0, affinity 0, not Last.
  EXPECT_EQ(redist_read(c, kGicrTyper, 8, frame0).value, 0U);
  // Frame 1: Processor_Number in [23:8], Aff0 in [39:32], Last set —
  // the guest's affinity walk (TYPER_HI == its MPIDR) must terminate here.
  const std::uint64_t t1 = redist_read(c, kGicrTyper, 8, frame1).value;
  EXPECT_EQ((t1 >> 8U) & 0xFFFFU, 1U);
  EXPECT_EQ(t1 >> 32U, 1U);
  EXPECT_NE(t1 & kGicrTyperLast, 0U);
  EXPECT_EQ(redist_read(c, kGicrTyperHi, 4, frame1).value, 1U);
}

TEST(VgicRedist, EnableSetAndClearAreOneSided) {
  RedistState c{};
  EXPECT_TRUE(redist_write(c, kGicrIsenabler0, 4, 1U << 27U));
  EXPECT_EQ(redist_read(c, kGicrIsenabler0, 4).value, 0xFFFFU | (1U << 27U));

  // Writing zeros through ISENABLER must not clear anything.
  EXPECT_TRUE(redist_write(c, kGicrIsenabler0, 4, 0));
  EXPECT_EQ(c.isenabler0, 0xFFFFU | (1U << 27U));

  EXPECT_TRUE(redist_write(c, kGicrIcenabler0, 4, 1U << 27U));
  EXPECT_EQ(c.isenabler0, 0xFFFFU);
}

TEST(VgicRedist, PendingSetAndClear) {
  RedistState c{};
  EXPECT_TRUE(redist_write(c, kGicrIspendr0, 4, 1U << 5U));
  EXPECT_EQ(c.pending, 1U << 5U);
  EXPECT_TRUE(redist_write(c, kGicrIcpendr0, 4, 1U << 5U));
  EXPECT_EQ(c.pending, 0U);
}

TEST(VgicRedist, PriorityByteAndWordAccess) {
  RedistState c{};
  // Word write covering INTIDs 24..27 (offset 0x418).
  EXPECT_TRUE(redist_write(c, kGicrIpriorityr + 24, 4, 0xA0'80'40'20U));
  EXPECT_EQ(c.prio[24], 0x20U);
  EXPECT_EQ(c.prio[27], 0xA0U);

  // Single-byte access to INTID 27.
  EXPECT_TRUE(redist_write(c, kGicrIpriorityr + 27, 1, 0x60));
  EXPECT_EQ(redist_read(c, kGicrIpriorityr + 27, 1).value, 0x60U);
}

TEST(VgicRedist, IcfgrAcceptedAndIgnored) {
  RedistState c{};
  EXPECT_TRUE(redist_write(c, kGicrIcfgr1, 4, ~0U));
  EXPECT_EQ(redist_read(c, kGicrIcfgr1, 4).value, 0U);
}

TEST(VgicRedist, UnknownOffsetReported) {
  RedistState c{};
  EXPECT_FALSE(redist_read(c, 0x1F000, 4).known);
  EXPECT_FALSE(redist_write(c, 0x1F000, 4, 1));
}
