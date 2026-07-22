// tests/host/vgic_delivery_test.cpp
//
// Host-side GTest suite for the pure vGICv3 delivery logic
// (components/vgic/include/vgic_delivery.hpp): pending bitmap →
// list-register multiplexing. Register emulation is covered by
// vgic_model_test.cpp.

#include "vgic/vgic_delivery.hpp"

#include <gtest/gtest.h>

using namespace nova::vgic;

namespace {
constexpr std::size_t kLrs = 4; // QEMU-like list register count
} // namespace

TEST(VgicRefill, DeliversEnabledPendingIntid) {
  CpuState c{};
  c.redist.isenabler0 |= 1U << 27U;
  c.redist.pending = 1U << 27U;

  EXPECT_FALSE(refill(c, kLrs));
  EXPECT_EQ(c.redist.pending, 0U);
  EXPECT_TRUE(lr_in_flight(c.lr[0]));
  EXPECT_EQ(lr_vintid(c.lr[0]), 27U);
}

TEST(VgicRefill, DisabledIntidStaysPendingWithoutMaintenance) {
  CpuState c{};
  c.redist.pending = 1U << 27U; // PPI 27 not enabled

  EXPECT_FALSE(refill(c, kLrs)); // undeliverable — no maintenance spin
  EXPECT_EQ(c.redist.pending, 1U << 27U);
  EXPECT_FALSE(lr_in_flight(c.lr[0]));
}

TEST(VgicRefill, EnablingPendingPrivateIntidMakesItDeliverable) {
  CpuState c{};
  c.redist.pending = 1U << 20U;
  ASSERT_FALSE(refill(c, kLrs));

  c.redist.isenabler0 |= 1U << 20U;
  EXPECT_FALSE(refill(c, kLrs));
  EXPECT_EQ(c.redist.pending, 0U);
  EXPECT_EQ(lr_vintid(c.lr[0]), 20U);
}

TEST(VgicRefill, GroupZeroConfigurationStillDelivers) {
  // A secure-convention guest programs its interrupts as Group 0
  // (Zephyr writes IGROUPR0 = 0). The enable bit is the single
  // delivery gate — the injected LR is Group 1 either way.
  CpuState c{};
  c.redist.igroupr0 = 0;
  c.redist.isenabler0 |= 1U << 27U;
  c.redist.pending = 1U << 27U;

  EXPECT_FALSE(refill(c, kLrs));
  EXPECT_EQ(c.redist.pending, 0U);
  EXPECT_EQ(lr_vintid(c.lr[0]), 27U);
  EXPECT_NE(c.lr[0] & kLrGroup1, 0U);
}

TEST(VgicRefill, PriorityOrderThenIntidOrder) {
  CpuState c{};
  c.redist.isenabler0 = ~0U;
  c.redist.prio[3]    = 0x40;
  c.redist.prio[9]    = 0x20; // highest priority (lowest value)
  c.redist.prio[12]   = 0x40;
  c.redist.pending    = (1U << 3U) | (1U << 9U) | (1U << 12U);

  EXPECT_FALSE(refill(c, kLrs));
  EXPECT_EQ(lr_vintid(c.lr[0]), 9U);
  EXPECT_EQ(lr_vintid(c.lr[1]), 3U); // tie: lowest INTID first
  EXPECT_EQ(lr_vintid(c.lr[2]), 12U);
}

TEST(VgicRefill, InFlightDuplicateStaysPending) {
  CpuState c{};
  c.redist.pending = 1U << 0U;
  EXPECT_FALSE(refill(c, kLrs));
  EXPECT_EQ(lr_vintid(c.lr[0]), 0U);

  // Second edge while the first is still in flight: stays queued and
  // requests maintenance so it is injected once the LR frees up.
  c.redist.pending = 1U << 0U;
  EXPECT_TRUE(refill(c, kLrs));
  EXPECT_EQ(c.redist.pending, 1U << 0U);
  EXPECT_FALSE(lr_in_flight(c.lr[1]));

  // Guest consumed the first edge → LR freed → the queued one lands.
  c.lr[0] = 0;
  EXPECT_FALSE(refill(c, kLrs));
  EXPECT_EQ(lr_vintid(c.lr[0]), 0U);
  EXPECT_EQ(c.redist.pending, 0U);
}

TEST(VgicRefill, LrExhaustionRequestsMaintenance) {
  CpuState c{};
  c.redist.isenabler0 = ~0U;
  c.redist.pending    = 0x3F; // 6 pending, 4 LRs

  EXPECT_TRUE(refill(c, kLrs));
  for (std::size_t i = 0; i < kLrs; ++i) {
    EXPECT_TRUE(lr_in_flight(c.lr[i]));
  }
  EXPECT_EQ(c.redist.pending, 0x30U); // INTIDs 4 and 5 still queued
}

TEST(VgicSpiRefill, DeliversRoutedEnabledSpi) {
  CpuState  c{};
  DistState d{};
  d.spi_enabled = 1U << 1U; // INTID 33
  d.spi_pending = 1U << 1U;
  d.spi_prio[1] = 0x40;

  EXPECT_FALSE(refill(c, kLrs, &d, /*vcpu=*/0, /*vcpus=*/2));
  EXPECT_EQ(d.spi_pending, 0U);
  EXPECT_EQ(lr_vintid(c.lr[0]), 33U);
  EXPECT_EQ((c.lr[0] >> kLrPriorityShift) & 0xFFU, 0x40U);
}

TEST(VgicSpiRefill, RouteGatesTheTakingVcpu) {
  CpuState  c{};
  DistState d{};
  d.spi_enabled  = 1U << 1U;
  d.spi_pending  = 1U << 1U;
  d.spi_route[1] = 1; // IROUTER(33) → vCPU 1

  // vCPU 0 must not take it — and must not spin maintenance on it.
  EXPECT_FALSE(refill(c, kLrs, &d, 0, 2));
  EXPECT_EQ(d.spi_pending, 1U << 1U);
  EXPECT_FALSE(lr_in_flight(c.lr[0]));

  EXPECT_FALSE(refill(c, kLrs, &d, 1, 2));
  EXPECT_EQ(lr_vintid(c.lr[0]), 33U);
}

TEST(VgicSpiRefill, ReroutingPendingSpiChangesItsDeliverableTarget) {
  CpuState  target{};
  DistState dist{};
  dist.spi_enabled = 1U << 8U; // INTID 40
  dist.spi_pending = 1U << 8U;

  EXPECT_NE(spi_deliverable(dist, 0, 2), 0U);
  EXPECT_EQ(spi_deliverable(dist, 1, 2), 0U);

  dist.spi_route[8] = 1;
  EXPECT_EQ(spi_deliverable(dist, 0, 2), 0U);
  EXPECT_NE(spi_deliverable(dist, 1, 2), 0U);
  EXPECT_FALSE(refill(target, kLrs, &dist, 1, 2));
  EXPECT_EQ(lr_vintid(target.lr[0]), 40U);
}

TEST(VgicSpiRefill, DisabledSpiStaysPending) {
  CpuState  c{};
  DistState d{};
  d.spi_pending = 1U << 1U;

  EXPECT_FALSE(refill(c, kLrs, &d, 0, 1));
  EXPECT_EQ(d.spi_pending, 1U << 1U);
  EXPECT_FALSE(lr_in_flight(c.lr[0]));
}

TEST(VgicSpiRefill, PrioritiesInterleaveWithPrivate) {
  CpuState  c{};
  DistState d{};
  c.redist.isenabler0 = ~0U;
  c.redist.prio[27]   = 0x80;
  c.redist.pending    = 1U << 27U;
  d.spi_enabled       = 1U << 1U;
  d.spi_pending       = 1U << 1U;
  d.spi_prio[1]       = 0x20; // SPI 33 outranks PPI 27

  EXPECT_FALSE(refill(c, kLrs, &d, 0, 1));
  EXPECT_EQ(lr_vintid(c.lr[0]), 33U);
  EXPECT_EQ(lr_vintid(c.lr[1]), 27U);
}

TEST(VgicMakeLr, FieldEncoding) {
  const std::uint64_t lr = make_lr(27, 0x80);
  EXPECT_EQ(lr & kLrStateMask, kLrStatePending);
  EXPECT_NE(lr & kLrGroup1, 0U);
  EXPECT_EQ((lr >> kLrPriorityShift) & 0xFFU, 0x80U);
  EXPECT_EQ(lr_vintid(lr), 27U);
}
