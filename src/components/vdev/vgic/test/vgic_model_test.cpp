// Host-side GTest suite for the pure vGICv3 register model (the vgic
// component): the stateful half of GICD/GICR frame emulation, which
// dist_read/dist_write and redist_read/redist_write reach through
// mutable state rather than a constant expression. Reset values,
// identification words and the pure frame decoders are pinned in
// vgic_model.hpp; delivery is covered by vgic_delivery_test.cpp.

#include "vgic/vgic_model.hpp"

#include <gtest/gtest.h>

using namespace nova::vgic;

// ---------------------------------------------------------------------------
// Distributor frame
// ---------------------------------------------------------------------------

TEST(VgicDist, CtlrRoundTripsWithDsAlwaysSet) {
  DistState d{};
  EXPECT_TRUE(dist_write(d, kGicdCtlr, 4, 0x12));
  const auto r = dist_read(d, kGicdCtlr, 4);
  EXPECT_TRUE(r.known);
  EXPECT_EQ(r.value, 0x12U | kGicdCtlrDs);

  // DS is RO-set: a zero write cannot clear it.
  EXPECT_TRUE(dist_write(d, kGicdCtlr, 4, 0));
  EXPECT_EQ(dist_read(d, kGicdCtlr, 4).value, kGicdCtlrDs);
}

TEST(VgicDist, UnknownOffsetReported) {
  DistState d{};
  EXPECT_FALSE(dist_read(d, 0x0080, 4).known); // GICD_IGROUPR0: RES0 under ARE
  EXPECT_FALSE(dist_write(d, 0x0100, 4, ~0U)); // GICD_ISENABLER0: redistributor's job
}

TEST(VgicDist, SpiEnableSetAndClearAreOneSided) {
  DistState d{};
  EXPECT_TRUE(dist_write(d, kGicdIsenabler1, 4, 1U << 1U)); // INTID 33
  EXPECT_EQ(dist_read(d, kGicdIsenabler1, 4).value, 1U << 1U);

  // Writing zeros through ISENABLER must not clear anything.
  EXPECT_TRUE(dist_write(d, kGicdIsenabler1, 4, 0));
  EXPECT_EQ(d.spi_enabled, 1U << 1U);

  EXPECT_TRUE(dist_write(d, kGicdIcenabler1, 4, 1U << 1U));
  EXPECT_EQ(d.spi_enabled, 0U);
}

TEST(VgicDist, SpiPendingSetAndClear) {
  DistState d{};
  EXPECT_TRUE(dist_write(d, kGicdIspendr1, 4, 1U << 3U));
  EXPECT_EQ(d.spi_pending, 1U << 3U);
  EXPECT_EQ(dist_read(d, kGicdIcpendr1, 4).value, 1U << 3U);
  EXPECT_TRUE(dist_write(d, kGicdIcpendr1, 4, 1U << 3U));
  EXPECT_EQ(d.spi_pending, 0U);
}

TEST(VgicDist, SpiPriorityByteAndWordAccess) {
  DistState d{};
  // Word write covering INTIDs 32..35 (offset 0x420).
  EXPECT_TRUE(dist_write(d, kGicdIpriorityrSpi, 4, 0xA0'80'40'20U));
  EXPECT_EQ(d.spi_prio[0], 0x20U);
  EXPECT_EQ(d.spi_prio[3], 0xA0U);

  // Single-byte access to INTID 33.
  EXPECT_TRUE(dist_write(d, kGicdIpriorityrSpi + 1, 1, 0x60));
  EXPECT_EQ(dist_read(d, kGicdIpriorityrSpi + 1, 1).value, 0x60U);
}

TEST(VgicDist, IrouterKeepsAff0AndRoutesDelivery) {
  DistState d{};
  // IROUTER(33) = 0x6108: Aff0 stored, IRM/upper affinities dropped.
  const std::uint64_t off = kGicdIrouterSpi + 8U * (33U - kNumPrivate);
  EXPECT_TRUE(dist_write(d, off, 8, (1ULL << 31U) | 0x01U));
  EXPECT_EQ(dist_read(d, off, 8).value, 1U);
  EXPECT_EQ(dist_read(d, off + 4, 4).value, 0U); // high word RES0

  EXPECT_EQ(spi_target(d, 33, /*vcpus=*/2), 1U);
  EXPECT_EQ(spi_target(d, 33, /*vcpus=*/1), 0U); // clamp to the VM's width
  EXPECT_EQ(spi_target(d, 32, 2), 0U);           // reset route
}

// The list is the model's; what this covers is that both directions
// honour it — a write lands nowhere and the read that follows still
// answers zero.
TEST(VgicDist, EveryIgnoredOffsetAcceptsAWriteAndReadsZero) {
  DistState d{};
  for (const auto off : kDistIgnored) {
    EXPECT_TRUE(dist_write(d, off, 4, ~0U));
    EXPECT_EQ(dist_read(d, off, 4).value, 0U);
  }
}

TEST(VgicDist, Typer2ReadsAsZero) {
  const DistState d{};
  EXPECT_TRUE(dist_read(d, kGicdTyper2, 4).known); // no extended features
  EXPECT_EQ(dist_read(d, kGicdTyper2, 4).value, 0U);
}

// What the words hold is asserted where they are defined; what this
// covers is the read path to them. A driver probes the distributor
// through these three offsets and binds on what comes back, so a case
// answering out of a neighbour's constant is a mis-identified GIC while
// every constant in the header still reads correctly.
TEST(VgicDist, IdentificationWordsComeFromTheirOwnRegisters) {
  const DistState d{};

  const auto typer = dist_read(d, kGicdTyper, 4);
  const auto iidr  = dist_read(d, kGicdIidr, 4);
  const auto pidr2 = dist_read(d, kGicdPidr2, 4);

  ASSERT_TRUE(typer.known);
  ASSERT_TRUE(iidr.known);
  ASSERT_TRUE(pidr2.known);
  EXPECT_EQ(typer.value, kGicdTyperValue);
  EXPECT_EQ(iidr.value, kGicIidrValue);
  EXPECT_EQ(pidr2.value, kPidr2GicV3);
}

// ---------------------------------------------------------------------------
// Redistributor frame
// ---------------------------------------------------------------------------

// What redist_typer encodes is asserted where it is defined; what this
// covers is the read path to it — a 64-bit register the guest reaches as
// two words, whose halves must not come back swapped or duplicated.
TEST(VgicRedist, TyperIsReadableAsTwoWords) {
  RedistState        c{};
  constexpr RedistId kId{.number = 1, .last = true};

  const auto low  = redist_read(c, kGicrTyper, 4, kId);
  const auto high = redist_read(c, kGicrTyperHi, 4, kId);

  ASSERT_TRUE(low.known);
  ASSERT_TRUE(high.known);
  EXPECT_EQ(low.value, redist_typer(kId));
  EXPECT_EQ(high.value, redist_typer(kId) >> 32U);
}

TEST(VgicRedist, WakerHandshake) {
  RedistState c{};
  auto        r = redist_read(c, kGicrWaker, 4);
  EXPECT_EQ(r.value, kWakerProcessorSleep | kWakerChildrenAsleep);

  EXPECT_TRUE(redist_write(c, kGicrWaker, 4, 0));
  r = redist_read(c, kGicrWaker, 4);
  EXPECT_EQ(r.value, 0U); // ChildrenAsleep clears with ProcessorSleep
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

TEST(VgicRedist, EveryIgnoredOffsetAcceptsAWriteAndReadsZero) {
  RedistState c{};
  for (const auto off : kRedistIgnored) {
    EXPECT_TRUE(redist_write(c, off, 4, ~0U));
    EXPECT_EQ(redist_read(c, off, 4).value, 0U);
  }
}

TEST(VgicRedist, UnknownOffsetReported) {
  RedistState c{};
  EXPECT_FALSE(redist_read(c, 0x1F000, 4).known);
  EXPECT_FALSE(redist_write(c, 0x1F000, 4, 1));
}

// ---------------------------------------------------------------------------
// Delivery-effect classification (gates the component's reevaluate fan-out)
// ---------------------------------------------------------------------------

TEST(VgicDist, DeliveryEffectOnlyForEnablePendingRoute) {
  DistState d{};
  EXPECT_TRUE(dist_write(d, kGicdIsenabler1, 4, 1U).delivery);
  EXPECT_TRUE(dist_write(d, kGicdIcenabler1, 4, 1U).delivery);
  EXPECT_TRUE(dist_write(d, kGicdIspendr1, 4, 1U).delivery);
  EXPECT_TRUE(dist_write(d, kGicdIcpendr1, 4, 1U).delivery);
  EXPECT_TRUE(dist_write(d, kGicdIrouterSpi, 8, 1U).delivery); // aligned low word: route moves

  EXPECT_FALSE(dist_write(d, kGicdIrouterSpi + 4U, 4, 1U).delivery); // high word is WI
  EXPECT_FALSE(dist_write(d, kGicdCtlr, 4, 1U).delivery);
  EXPECT_FALSE(dist_write(d, kGicdIgroupr1, 4, ~0U).delivery);
  EXPECT_FALSE(dist_write(d, kGicdIpriorityrSpi, 4, 0x20U).delivery);
  EXPECT_FALSE(dist_write(d, kGicdIcfgr2, 4, ~0U).delivery);
  EXPECT_FALSE(dist_write(d, kGicdIsactiver1, 4, ~0U).delivery);
  EXPECT_FALSE(dist_write(d, 0xF000, 4, 1U).delivery); // unknown: no effect at all
}

TEST(VgicRedist, DeliveryEffectOnlyForEnableAndPending) {
  RedistState r{};
  EXPECT_TRUE(redist_write(r, kGicrIsenabler0, 4, 1U).delivery);
  EXPECT_TRUE(redist_write(r, kGicrIcenabler0, 4, 1U).delivery);
  EXPECT_TRUE(redist_write(r, kGicrIspendr0, 4, 1U).delivery);
  EXPECT_TRUE(redist_write(r, kGicrIcpendr0, 4, 1U).delivery);

  EXPECT_FALSE(redist_write(r, kGicrWaker, 4, 0U).delivery);
  EXPECT_FALSE(redist_write(r, kGicrIgroupr0, 4, ~0U).delivery);
  EXPECT_FALSE(redist_write(r, kGicrIpriorityr, 4, 0x20U).delivery);
  EXPECT_FALSE(redist_write(r, kGicrIcfgr1, 4, ~0U).delivery);
}

TEST(VgicDist, IcpendrClearKeepsTokenProtectedSpis) {
  DistState d{};
  ASSERT_TRUE(dist_write(d, kGicdIspendr1, 4, 0b0111U));

  // Bits 0 and 2 carry live EoI tokens (not yet in any LR): a full
  // clear may drop only the unprotected bit 1.
  EXPECT_TRUE(dist_write(d, kGicdIcpendr1, 4, ~0U, /*keep_pending=*/0b0101U));
  EXPECT_EQ(d.spi_pending, 0b0101U);

  // Without protection the clear behaves architecturally.
  EXPECT_TRUE(dist_write(d, kGicdIcpendr1, 4, 0b0001U));
  EXPECT_EQ(d.spi_pending, 0b0100U);
}
