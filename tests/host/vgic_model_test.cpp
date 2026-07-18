// tests/host/vgic_model_test.cpp
//
// Host-side GTest suite for the pure vGICv3 model
// (components/vgic/include/vgic_model.hpp): register emulation on the
// GICD/GICR frames and the pending-bitmap → list-register delivery
// logic.

#include "components/vgic/include/vgic_model.hpp"

#include <gtest/gtest.h>

using namespace nova::vgic;

namespace {
constexpr std::size_t kLrs = 4; // QEMU-like list register count
} // namespace

// ---------------------------------------------------------------------------
// Reset state
// ---------------------------------------------------------------------------

TEST(VgicReset, SgisEnabledPpisDisabledAllGroup1) {
  const CpuState c{};
  EXPECT_EQ(c.redist.isenabler0, 0xFFFFU);
  EXPECT_EQ(c.redist.igroupr0, ~0U);
  EXPECT_EQ(c.pending, 0U);
  EXPECT_TRUE(c.redist.asleep);
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
  CpuState c{};
  auto     r = redist_read(c, kGicrWaker, 4);
  EXPECT_EQ(r.value, kWakerProcessorSleep | kWakerChildrenAsleep);

  EXPECT_TRUE(redist_write(c, kGicrWaker, 4, 0));
  r = redist_read(c, kGicrWaker, 4);
  EXPECT_EQ(r.value, 0U); // ChildrenAsleep clears with ProcessorSleep
}

TEST(VgicRedist, TyperReportsLast) {
  const CpuState c{};
  EXPECT_EQ(redist_read(c, kGicrTyper, 8).value, static_cast<std::uint64_t>(kGicrTyperLast));
  EXPECT_EQ(redist_read(c, kGicrTyperHi, 4).value, 0U);
}

TEST(VgicRedist, EnableSetAndClearAreOneSided) {
  CpuState c{};
  EXPECT_TRUE(redist_write(c, kGicrIsenabler0, 4, 1U << 27U));
  EXPECT_EQ(redist_read(c, kGicrIsenabler0, 4).value, 0xFFFFU | (1U << 27U));

  // Writing zeros through ISENABLER must not clear anything.
  EXPECT_TRUE(redist_write(c, kGicrIsenabler0, 4, 0));
  EXPECT_EQ(c.redist.isenabler0, 0xFFFFU | (1U << 27U));

  EXPECT_TRUE(redist_write(c, kGicrIcenabler0, 4, 1U << 27U));
  EXPECT_EQ(c.redist.isenabler0, 0xFFFFU);
}

TEST(VgicRedist, PendingSetAndClear) {
  CpuState c{};
  EXPECT_TRUE(redist_write(c, kGicrIspendr0, 4, 1U << 5U));
  EXPECT_EQ(c.pending, 1U << 5U);
  EXPECT_TRUE(redist_write(c, kGicrIcpendr0, 4, 1U << 5U));
  EXPECT_EQ(c.pending, 0U);
}

TEST(VgicRedist, PriorityByteAndWordAccess) {
  CpuState c{};
  // Word write covering INTIDs 24..27 (offset 0x418).
  EXPECT_TRUE(redist_write(c, kGicrIpriorityr + 24, 4, 0xA0'80'40'20U));
  EXPECT_EQ(c.redist.prio[24], 0x20U);
  EXPECT_EQ(c.redist.prio[27], 0xA0U);

  // Single-byte access to INTID 27.
  EXPECT_TRUE(redist_write(c, kGicrIpriorityr + 27, 1, 0x60));
  EXPECT_EQ(redist_read(c, kGicrIpriorityr + 27, 1).value, 0x60U);
}

TEST(VgicRedist, IcfgrAcceptedAndIgnored) {
  CpuState c{};
  EXPECT_TRUE(redist_write(c, kGicrIcfgr1, 4, ~0U));
  EXPECT_EQ(redist_read(c, kGicrIcfgr1, 4).value, 0U);
}

TEST(VgicRedist, UnknownOffsetReported) {
  CpuState c{};
  EXPECT_FALSE(redist_read(c, 0x1F000, 4).known);
  EXPECT_FALSE(redist_write(c, 0x1F000, 4, 1));
}

// ---------------------------------------------------------------------------
// Delivery: pending bitmap → list registers
// ---------------------------------------------------------------------------

TEST(VgicRefill, DeliversEnabledPendingIntid) {
  CpuState c{};
  c.redist.isenabler0 |= 1U << 27U;
  c.pending = 1U << 27U;

  EXPECT_FALSE(refill(c, kLrs));
  EXPECT_EQ(c.pending, 0U);
  EXPECT_TRUE(lr_in_flight(c.lr[0]));
  EXPECT_EQ(lr_vintid(c.lr[0]), 27U);
}

TEST(VgicRefill, DisabledIntidStaysPendingWithoutMaintenance) {
  CpuState c{};
  c.pending = 1U << 27U; // PPI 27 not enabled

  EXPECT_FALSE(refill(c, kLrs)); // undeliverable — no maintenance spin
  EXPECT_EQ(c.pending, 1U << 27U);
  EXPECT_FALSE(lr_in_flight(c.lr[0]));
}

TEST(VgicRefill, PriorityOrderThenIntidOrder) {
  CpuState c{};
  c.redist.isenabler0 = ~0U;
  c.redist.prio[3]    = 0x40;
  c.redist.prio[9]    = 0x20; // highest priority (lowest value)
  c.redist.prio[12]   = 0x40;
  c.pending           = (1U << 3U) | (1U << 9U) | (1U << 12U);

  EXPECT_FALSE(refill(c, kLrs));
  EXPECT_EQ(lr_vintid(c.lr[0]), 9U);
  EXPECT_EQ(lr_vintid(c.lr[1]), 3U); // tie: lowest INTID first
  EXPECT_EQ(lr_vintid(c.lr[2]), 12U);
}

TEST(VgicRefill, InFlightDuplicateStaysPending) {
  CpuState c{};
  c.pending = 1U << 0U;
  EXPECT_FALSE(refill(c, kLrs));
  EXPECT_EQ(lr_vintid(c.lr[0]), 0U);

  // Second edge while the first is still in flight: stays queued and
  // requests maintenance so it is injected once the LR frees up.
  c.pending = 1U << 0U;
  EXPECT_TRUE(refill(c, kLrs));
  EXPECT_EQ(c.pending, 1U << 0U);
  EXPECT_FALSE(lr_in_flight(c.lr[1]));

  // Guest consumed the first edge → LR freed → the queued one lands.
  c.lr[0] = 0;
  EXPECT_FALSE(refill(c, kLrs));
  EXPECT_EQ(lr_vintid(c.lr[0]), 0U);
  EXPECT_EQ(c.pending, 0U);
}

TEST(VgicRefill, LrExhaustionRequestsMaintenance) {
  CpuState c{};
  c.redist.isenabler0 = ~0U;
  c.pending           = 0x3F; // 6 pending, 4 LRs

  EXPECT_TRUE(refill(c, kLrs));
  for (std::size_t i = 0; i < kLrs; ++i) {
    EXPECT_TRUE(lr_in_flight(c.lr[i]));
  }
  EXPECT_EQ(c.pending, 0x30U); // INTIDs 4 and 5 still queued
}

TEST(VgicMakeLr, FieldEncoding) {
  const std::uint64_t lr = make_lr(27, 0x80);
  EXPECT_EQ(lr & kLrStateMask, kLrStatePending);
  EXPECT_NE(lr & kLrGroup1, 0U);
  EXPECT_EQ((lr >> kLrPriorityShift) & 0xFFU, 0x80U);
  EXPECT_EQ(lr_vintid(lr), 27U);
}
