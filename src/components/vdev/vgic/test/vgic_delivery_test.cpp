// Host-side GTest suite for the pure vGICv3 delivery logic: pending
// bitmap → list-register multiplexing, over the multi-step sequences
// that exposed real delivery losses (a re-asserted timer behind an
// active copy, a duplicate INTID waiting on an EOI). The single-call
// encodings it builds on are pinned in vgic_delivery.hpp; register
// emulation is covered by vgic_model_test.cpp.

#include "vgic/vgic_delivery.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
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

TEST(VgicRefill, InFlightDuplicateStaysPendingAndArmsEoiMaintenance) {
  CpuState c{};
  c.redist.pending = 1U << 0U;
  EXPECT_FALSE(refill(c, kLrs));
  EXPECT_EQ(lr_vintid(c.lr[0]), 0U);
  EXPECT_EQ(c.lr[0] & kLrEoi, 0U); // nothing queued behind it yet

  // Second edge while the first is still in flight stays queued without
  // underflow maintenance, and arms EOI maintenance on the LR holding
  // the first — its deactivate is the exit that retries the queued one.
  c.redist.pending = 1U << 0U;
  EXPECT_FALSE(refill(c, kLrs));
  EXPECT_EQ(c.redist.pending, 1U << 0U);
  EXPECT_FALSE(lr_in_flight(c.lr[1]));
  EXPECT_NE(c.lr[0] & kLrEoi, 0U);

  // Guest consumed the first edge → LR freed → the queued one lands.
  c.lr[0] = 0;
  EXPECT_FALSE(refill(c, kLrs));
  EXPECT_EQ(lr_vintid(c.lr[0]), 0U);
  EXPECT_EQ(c.redist.pending, 0U);
}

// The guest virtual timer re-asserting while its injected copy is still
// active is the case that must never silently drop: EL2 masks CNTV when
// it takes the physical PPI, so a lost re-injection costs the guest its
// re-arm and with it every future timer interrupt. Free LRs mean no
// underflow maintenance is coming, and a compute-bound guest issues no
// wfi — the armed EOI maintenance is the only path back.
TEST(VgicRefill, ReassertedTimerSurvivesAnActiveInFlightCopy) {
  constexpr std::uint32_t kTimer = 27;
  CpuState                c{};
  c.redist.isenabler0 |= 1U << kTimer;
  c.redist.pending = 1U << kTimer;
  ASSERT_FALSE(refill(c, kLrs));
  ASSERT_EQ(lr_vintid(c.lr[0]), kTimer);

  c.lr[0] = (c.lr[0] & ~kLrStateMask) | kLrStateActive; // guest acknowledged it

  c.redist.pending = 1U << kTimer; // CNTV asserted again inside the handler
  EXPECT_FALSE(refill(c, kLrs));
  EXPECT_EQ(c.redist.pending, 1U << kTimer);
  EXPECT_NE(c.lr[0] & kLrEoi, 0U);

  // Deactivate → EISR bit 0 → harvest frees the slot → the copy lands.
  c.lr[0] &= ~kLrStateMask;
  const EoiHarvest harvest = harvest_eois(c, 1U, kLrs);
  EXPECT_EQ(harvest.count, 0U); // private INTID: no device EoI to settle
  EXPECT_FALSE(refill(c, kLrs));
  EXPECT_EQ(lr_vintid(c.lr[0]), kTimer);
  EXPECT_EQ(c.redist.pending, 0U);
}

TEST(VgicRefill, DistinctIntidTakesAFreeLrWithoutArmingMaintenance) {
  CpuState c{};
  c.redist.pending = 1U << 5U;
  ASSERT_FALSE(refill(c, kLrs));
  ASSERT_EQ(lr_vintid(c.lr[0]), 5U);

  c.redist.pending = 1U << 9U; // different INTID — a free LR takes it
  EXPECT_FALSE(refill(c, kLrs));
  EXPECT_EQ(lr_vintid(c.lr[1]), 9U);
  EXPECT_EQ(c.lr[0] & kLrEoi, 0U);
  EXPECT_EQ(c.lr[1] & kLrEoi, 0U);
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

TEST(VgicSpiRefill, InFlightDuplicateArmsEoiMaintenance) {
  CpuState  c{};
  DistState d{};
  d.spi_enabled = 1U << 1U; // INTID 33
  d.spi_pending = 1U << 1U;
  ASSERT_FALSE(refill(c, kLrs, &d, 0, 1));
  ASSERT_EQ(lr_vintid(c.lr[0]), 33U);
  EXPECT_EQ(c.lr[0] & kLrEoi, 0U); // untracked SPI: no device token

  d.spi_pending = 1U << 1U; // re-asserted before the guest retired it
  EXPECT_FALSE(refill(c, kLrs, &d, 0, 1));
  EXPECT_EQ(d.spi_pending, 1U << 1U);
  EXPECT_NE(c.lr[0] & kLrEoi, 0U);
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

TEST(VgicSpiRefill, MovesTrackedLevelTokenIntoEoiMaintenanceLr) {
  CpuState                       c{};
  DistState                      d{};
  std::array<EoiToken, kNumSpis> tokens{};
  d.spi_enabled = 1U << 5U;
  d.spi_pending = 1U << 5U;
  tokens[5]     = {.virtual_intid = 37, .physical_intid = 37, .generation = 4};

  EXPECT_FALSE(refill(c, kLrs, &d, 0, 1, &tokens));
  EXPECT_EQ(lr_vintid(c.lr[0]), 37U);
  EXPECT_NE(c.lr[0] & kLrEoi, 0U);
  EXPECT_EQ(c.lr_token[0].virtual_intid, 37U);
  EXPECT_EQ(c.lr_token[0].physical_intid, 37U);
  EXPECT_EQ(c.lr_token[0].generation, 4U);
  EXPECT_FALSE(tokens[5].valid());

  const EoiToken completed = take_eoi_token(c, 0);
  EXPECT_EQ(completed.virtual_intid, 37U);
  EXPECT_EQ(completed.physical_intid, 37U);
  EXPECT_EQ(completed.generation, 4U);
  EXPECT_FALSE(take_eoi_token(c, 0).valid());
}

// ---------------------------------------------------------------------------
// EoI harvest
// ---------------------------------------------------------------------------

namespace {

// A resident vCPU with `count` in-flight LRs, each carrying a token.
auto make_tracked_cpu(std::size_t count) -> CpuState {
  CpuState c{};
  for (std::size_t i = 0; i < count; ++i) {
    const auto intid = static_cast<std::uint32_t>(kNumPrivate + i);
    c.lr[i]          = make_lr(intid, 0x40, true);
    c.lr_token[i]    = {.virtual_intid = intid, .physical_intid = intid, .generation = i + 1};
  }
  return c;
}

} // namespace

TEST(VgicHarvestEois, HarvestsOnlyTheEisrMarkedSlots) {
  CpuState   c       = make_tracked_cpu(kLrs);
  const auto harvest = harvest_eois(c, 0b0101U, kLrs);

  ASSERT_EQ(harvest.count, 2);
  EXPECT_EQ(harvest.tokens[0].virtual_intid, kNumPrivate);
  EXPECT_EQ(harvest.tokens[1].virtual_intid, kNumPrivate + 2);
  EXPECT_EQ(harvest.cleared, 0b0101U);

  // Marked slots are emptied in the shadow and their tokens consumed;
  // the untouched slots keep both.
  EXPECT_EQ(c.lr[0], 0U);
  EXPECT_EQ(c.lr[2], 0U);
  EXPECT_FALSE(c.lr_token[0].valid());
  EXPECT_FALSE(c.lr_token[2].valid());
  EXPECT_TRUE(lr_in_flight(c.lr[1]));
  EXPECT_TRUE(c.lr_token[1].valid());
  EXPECT_TRUE(lr_in_flight(c.lr[3]));
  EXPECT_TRUE(c.lr_token[3].valid());
}

TEST(VgicHarvestEois, UntrackedSlotIsClearedWithoutAToken) {
  CpuState c    = make_tracked_cpu(2);
  c.lr_token[1] = {}; // an untracked (guest-only) interrupt

  const auto harvest = harvest_eois(c, 0b11U, kLrs);
  ASSERT_EQ(harvest.count, 1);
  EXPECT_EQ(harvest.tokens[0].virtual_intid, kNumPrivate);
  EXPECT_EQ(harvest.cleared, 0b11U); // both slots freed regardless
  EXPECT_EQ(c.lr[1], 0U);
}

TEST(VgicHarvestEois, IdleEisrLeavesTheShadowUntouched) {
  CpuState   c       = make_tracked_cpu(kLrs);
  const auto before  = c;
  const auto harvest = harvest_eois(c, 0U, kLrs);

  EXPECT_EQ(harvest.count, 0);
  EXPECT_EQ(harvest.cleared, 0U);
  EXPECT_EQ(c.lr, before.lr);
  EXPECT_TRUE(c.lr_token[0].valid());
}

TEST(VgicHarvestEois, IgnoresBitsBeyondTheImplementedLrCount) {
  CpuState c = make_tracked_cpu(kMaxLrs);
  // EISR bits for slots the implementation does not have must not
  // consume the shadow entries sitting at those indices.
  const auto harvest = harvest_eois(c, ~0ULL, kLrs);

  EXPECT_EQ(harvest.count, kLrs);
  EXPECT_EQ(harvest.cleared, (1ULL << kLrs) - 1U);
  EXPECT_TRUE(lr_in_flight(c.lr[kLrs]));
  EXPECT_TRUE(c.lr_token[kLrs].valid());
}

TEST(VgicHarvestEois, LrCountIsClampedToTheShadowSize) {
  CpuState   c       = make_tracked_cpu(kMaxLrs);
  const auto harvest = harvest_eois(c, ~0ULL, kMaxLrs + 8);

  EXPECT_EQ(harvest.count, kMaxLrs);
  EXPECT_EQ(harvest.cleared, (1ULL << kMaxLrs) - 1U);
}

TEST(VgicHarvestEois, HarvestedSlotIsReusableByTheNextRefill) {
  CpuState                       c{};
  DistState                      d{};
  std::array<EoiToken, kNumSpis> tokens{};
  d.spi_enabled = 1U << 5U;
  d.spi_pending = 1U << 5U;
  tokens[5]     = {.virtual_intid = 37, .physical_intid = 37, .generation = 4};
  ASSERT_FALSE(refill(c, kLrs, &d, 0, 1, &tokens));
  ASSERT_EQ(lr_vintid(c.lr[0]), 37U);

  const auto harvest = harvest_eois(c, 0b1U, kLrs);
  ASSERT_EQ(harvest.count, 1);
  EXPECT_EQ(harvest.tokens[0].generation, 4U);

  // The freed slot takes the next candidate.
  d.spi_pending = 1U << 5U;
  EXPECT_FALSE(refill(c, kLrs, &d, 0, 1, &tokens));
  EXPECT_EQ(lr_vintid(c.lr[0]), 37U);
  EXPECT_FALSE(c.lr_token[0].valid()); // token already consumed by the harvest
}
