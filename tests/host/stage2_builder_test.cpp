// tests/host/stage2_builder_test.cpp
//
// Host GTest for the Stage 2 identity-map builder
// (components/core_mmu/include/stage2_builder.hpp).
//
// Covers the Phase 5/6 single-window scenario (IPA 0x5000_0000, 1 MiB),
// the L2 Block path for 2 MiB-aligned chunks, multi-range composition,
// and the failure modes (pool exhaustion, 1 GiB crossing, overlap,
// bad alignment).

#include "components/core_mmu/include/stage2_builder.hpp"

#include <array>
#include <gtest/gtest.h>

using namespace nova::mmu;

namespace {

// Arbitrary distinct non-zero PAs. The hypervisor uses real link-time
// addresses; the builder treats these as opaque frame identifiers.
constexpr std::uint64_t kFakeL2Pa = 0x0000'0000'0001'0000ULL;

constexpr std::size_t                          kPoolSize = 4;
constexpr std::array<std::uint64_t, kPoolSize> kFakeL3Pas{0x2'0000ULL, 0x3'0000ULL, 0x4'0000ULL, 0x5'0000ULL};

// Phase 5/6 guest layout.
constexpr std::uint64_t kIpaBase = 0x5000'0000ULL;
constexpr std::uint64_t kIpaSize = 0x0010'0000ULL; // 1 MiB = 256 pages

} // namespace

// ---------------------------------------------------------------------------
// Index extractors — bit slice behavior
// ---------------------------------------------------------------------------

TEST(Stage2Builder, L1IndexExtractsBits38To30) {
  EXPECT_EQ(l1_index(0), 0U);
  EXPECT_EQ(l1_index(k1GiB), 1U);
  EXPECT_EQ(l1_index(2ULL * k1GiB), 2U);
  // 0x5000_0000 = 1.25 GiB → L1 index 1
  EXPECT_EQ(l1_index(kIpaBase), 1U);
  // Lower bits below 1 GiB must not leak in
  EXPECT_EQ(l1_index(0x4FFF'FFFFULL), 1U);
  EXPECT_EQ(l1_index(0x7FFF'FFFFULL), 1U);
}

TEST(Stage2Builder, L2IndexExtractsBits29To21) {
  EXPECT_EQ(l2_index(0), 0U);
  EXPECT_EQ(l2_index(k2MiB), 1U);
  // 0x5000_0000 → (0x5000_0000 >> 21) & 0x1FF = 0x280 & 0x1FF = 128
  EXPECT_EQ(l2_index(kIpaBase), 128U);
}

TEST(Stage2Builder, L3IndexExtractsBits20To12) {
  EXPECT_EQ(l3_index(0), 0U);
  EXPECT_EQ(l3_index(k4KiB), 1U);
  EXPECT_EQ(l3_index(kIpaBase), 0U); // IPA base is 2 MiB-aligned → L3 starts at 0
  EXPECT_EQ(l3_index(kIpaBase + k4KiB), 1U);
  EXPECT_EQ(l3_index(kIpaBase + (255U * k4KiB)), 255U);
}

// ---------------------------------------------------------------------------
// Shared fixture
// ---------------------------------------------------------------------------

class IdentityMapFixture : public ::testing::Test {
protected:
  Table                        l1{};
  Table                        l2{};
  std::array<Table, kPoolSize> pool{};
  Stage2Tables tables{.l1 = &l1, .l2 = &l2, .l2_pa = kFakeL2Pa, .l3_pool = pool, .l3_pool_pas = kFakeL3Pas};

  [[nodiscard]] bool build(std::uint64_t base = kIpaBase, std::uint64_t size = kIpaSize,
                           std::uint64_t attrs = desc::kAttrNormalRwx) {
    return build_identity_map(tables, base, size, attrs);
  }
};

// ---------------------------------------------------------------------------
// build_identity_map — Phase 5/6 exact scenario (sub-2 MiB window)
// ---------------------------------------------------------------------------

TEST_F(IdentityMapFixture, L1HasOnlyOneValidTableEntry) {
  ASSERT_TRUE(build());

  // L1[1] is the only non-zero entry.
  EXPECT_EQ(descriptor_type(l1[1]), desc::kTypeTable);
  EXPECT_EQ(output_addr(l1[1]), kFakeL2Pa);
  EXPECT_TRUE(is_valid(l1[1]));

  for (std::size_t i = 0; i < kTableEntries; ++i) {
    if (i == 1) {
      continue;
    }
    EXPECT_EQ(l1[i], kInvalid) << "L1[" << i << "] should be invalid";
  }
}

TEST_F(IdentityMapFixture, L2HasOnlyOneValidTableEntry) {
  ASSERT_TRUE(build());

  // 1 MiB < 2 MiB → pages via the first pool table, not a Block.
  EXPECT_EQ(descriptor_type(l2[128]), desc::kTypeTable);
  EXPECT_EQ(output_addr(l2[128]), kFakeL3Pas[0]);
  EXPECT_EQ(tables.l3_used, 1U);

  for (std::size_t i = 0; i < kTableEntries; ++i) {
    if (i == 128) {
      continue;
    }
    EXPECT_EQ(l2[i], kInvalid) << "L2[" << i << "] should be invalid";
  }
}

TEST_F(IdentityMapFixture, L3HasTwoHundredFiftySixIdentityPages) {
  ASSERT_TRUE(build());

  // 256 pages [0..255] map IPA → PA identity, carry the leaf attrs.
  for (std::size_t i = 0; i < 256; ++i) {
    const std::uint64_t expected_pa = kIpaBase + (static_cast<std::uint64_t>(i) * k4KiB);
    SCOPED_TRACE("L3 page " + std::to_string(i));
    EXPECT_EQ(descriptor_type(pool[0][i]), desc::kTypePage);
    EXPECT_EQ(output_addr(pool[0][i]), expected_pa);
    EXPECT_TRUE(access_flag(pool[0][i]));
    EXPECT_FALSE(execute_never(pool[0][i])); // Rwx preset
    EXPECT_EQ(mem_attr(pool[0][i]), desc::kMemAttrNormalWB);
    EXPECT_EQ(s2ap(pool[0][i]), desc::kS2apReadWrite);
  }

  // Entries 256..511 are invalid (beyond the 1 MiB window).
  for (std::size_t i = 256; i < kTableEntries; ++i) {
    EXPECT_EQ(pool[0][i], kInvalid) << "L3[" << i << "] should be invalid";
  }
}

TEST_F(IdentityMapFixture, ZerosTablesBeforePopulating) {
  l1.fill(0xDEAD'BEEF'DEAD'BEEFULL);
  l2.fill(0xDEAD'BEEF'DEAD'BEEFULL);
  pool[0].fill(0xDEAD'BEEF'DEAD'BEEFULL);

  ASSERT_TRUE(build());

  // Spot check: entries that must be invalid really are zero, not
  // leftover sentinel.
  EXPECT_EQ(l1[0], 0ULL);
  EXPECT_EQ(l2[0], 0ULL);
  EXPECT_EQ(pool[0][256], 0ULL);
  EXPECT_EQ(pool[0][511], 0ULL);
}

TEST_F(IdentityMapFixture, AcceptsXnDataAttrs) {
  // Map a single 4 KiB page with XN data attributes.
  ASSERT_TRUE(build(kIpaBase, k4KiB, desc::kAttrNormalRwData));

  EXPECT_TRUE(execute_never(pool[0][0]));
  EXPECT_EQ(output_addr(pool[0][0]), kIpaBase);
  EXPECT_EQ(pool[0][1], kInvalid); // only one page mapped
}

// ---------------------------------------------------------------------------
// L2 Block path — 2 MiB-aligned chunks skip the L3 pool entirely
// ---------------------------------------------------------------------------

TEST_F(IdentityMapFixture, Exact2MibRangeBecomesOneBlock) {
  ASSERT_TRUE(build(kIpaBase, k2MiB));

  EXPECT_EQ(descriptor_type(l2[128]), desc::kTypeBlock);
  EXPECT_EQ(output_addr(l2[128]), kIpaBase);
  EXPECT_EQ(tables.l3_used, 0U); // no L3 spent
}

TEST_F(IdentityMapFixture, Large32MibRangeUsesSixteenBlocks) {
  // Phase 8-sized guest image: 32 MiB, 2 MiB-aligned.
  ASSERT_TRUE(build(kIpaBase, 16 * k2MiB)); // 32 MiB

  for (std::size_t i = 0; i < 16; ++i) {
    SCOPED_TRACE("L2 block " + std::to_string(128 + i));
    EXPECT_EQ(descriptor_type(l2[128 + i]), desc::kTypeBlock);
    EXPECT_EQ(output_addr(l2[128 + i]), kIpaBase + (i * k2MiB));
  }
  EXPECT_EQ(l2[144], kInvalid);
  EXPECT_EQ(tables.l3_used, 0U);
}

TEST_F(IdentityMapFixture, UnalignedTailFallsBackToPages) {
  // 3 MiB: one Block (2 MiB) + 256 pages via one pool table.
  ASSERT_TRUE(build(kIpaBase, k2MiB + kIpaSize));

  EXPECT_EQ(descriptor_type(l2[128]), desc::kTypeBlock);
  EXPECT_EQ(descriptor_type(l2[129]), desc::kTypeTable);
  EXPECT_EQ(output_addr(l2[129]), kFakeL3Pas[0]);
  EXPECT_EQ(tables.l3_used, 1U);
  EXPECT_EQ(output_addr(pool[0][0]), kIpaBase + k2MiB);
  EXPECT_EQ(output_addr(pool[0][255]), kIpaBase + k2MiB + (255U * k4KiB));
  EXPECT_EQ(pool[0][256], kInvalid);
}

TEST_F(IdentityMapFixture, UnalignedHeadCrossesInto2MibSlots) {
  // Start 1 MiB into a 2 MiB slot, span 4 MiB total:
  // head 1 MiB pages + one Block + tail 1 MiB pages.
  const std::uint64_t base = kIpaBase + kIpaSize; // 0x5010_0000
  ASSERT_TRUE(build(base, 4 * kIpaSize));

  EXPECT_EQ(descriptor_type(l2[128]), desc::kTypeTable); // head pages
  EXPECT_EQ(descriptor_type(l2[129]), desc::kTypeBlock); // aligned middle
  EXPECT_EQ(descriptor_type(l2[130]), desc::kTypeTable); // tail pages
  EXPECT_EQ(tables.l3_used, 2U);
  // Head pages sit in the upper half of pool[0].
  EXPECT_EQ(pool[0][0], kInvalid);
  EXPECT_EQ(output_addr(pool[0][256]), base);
  // Tail pages [0x5040_0000, 0x5050_0000) fill the lower half of pool[1].
  EXPECT_EQ(output_addr(pool[1][0]), base + (3 * kIpaSize));
  EXPECT_EQ(pool[1][256], kInvalid);
}

// ---------------------------------------------------------------------------
// Multi-range composition
// ---------------------------------------------------------------------------

TEST_F(IdentityMapFixture, TwoDisjointRangesShareTables) {
  init_tables(tables);
  ASSERT_TRUE(map_identity_range(tables, kIpaBase, kIpaSize, desc::kAttrNormalRwx));
  ASSERT_TRUE(map_identity_range(tables, kIpaBase + (4 * k2MiB), k2MiB, desc::kAttrDeviceRw));

  EXPECT_EQ(descriptor_type(l2[128]), desc::kTypeTable); // first range: pages
  EXPECT_EQ(descriptor_type(l2[132]), desc::kTypeBlock); // second range: block
  EXPECT_TRUE(execute_never(l2[132]));                   // device attrs preserved
  EXPECT_EQ(mem_attr(l2[132]), desc::kMemAttrDevice_nGnRE);
  // First range untouched by the second call.
  EXPECT_EQ(output_addr(pool[0][0]), kIpaBase);
}

TEST_F(IdentityMapFixture, SecondRangeInSameSlotReusesL3Table) {
  init_tables(tables);
  ASSERT_TRUE(map_identity_range(tables, kIpaBase, kIpaSize, desc::kAttrNormalRwx));
  // Upper half of the same 2 MiB slot.
  ASSERT_TRUE(map_identity_range(tables, kIpaBase + kIpaSize, kIpaSize, desc::kAttrNormalRwData));

  EXPECT_EQ(tables.l3_used, 1U); // same pool table serves both
  EXPECT_FALSE(execute_never(pool[0][0]));
  EXPECT_TRUE(execute_never(pool[0][256]));
}

// ---------------------------------------------------------------------------
// map_range — translated (non-identity) mappings
// ---------------------------------------------------------------------------

TEST_F(IdentityMapFixture, TranslatedPagesCarryPaOffset) {
  // Guest slot 1 layout: same IPA window, PA displaced by one 2 MiB stride.
  const std::uint64_t pa_base = kIpaBase + (2 * k2MiB);
  init_tables(tables);
  ASSERT_TRUE(map_range(tables, kIpaBase, pa_base, kIpaSize, desc::kAttrNormalRwx));

  EXPECT_EQ(descriptor_type(l2[128]), desc::kTypeTable);
  for (std::size_t i = 0; i < 256; i += 51) { // spot-check across the window
    SCOPED_TRACE("L3 page " + std::to_string(i));
    EXPECT_EQ(output_addr(pool[0][i]), pa_base + (static_cast<std::uint64_t>(i) * k4KiB));
  }
  EXPECT_EQ(pool[0][256], kInvalid);
}

TEST_F(IdentityMapFixture, TranslatedBlockKeepsPaAlignment) {
  const std::uint64_t pa_base = kIpaBase + (4 * k2MiB);
  init_tables(tables);
  ASSERT_TRUE(map_range(tables, kIpaBase, pa_base, k2MiB, desc::kAttrNormalRwx));

  EXPECT_EQ(descriptor_type(l2[128]), desc::kTypeBlock);
  EXPECT_EQ(output_addr(l2[128]), pa_base);
  EXPECT_EQ(tables.l3_used, 0U);
}

TEST_F(IdentityMapFixture, MisalignedPaOffsetFallsBackToPages) {
  // IPA slot is 2 MiB-aligned but the PA is displaced by 4 KiB — a Block
  // cannot encode that, so the whole 2 MiB goes through one L3 table.
  const std::uint64_t pa_base = kIpaBase + (2 * k2MiB) + k4KiB;
  init_tables(tables);
  ASSERT_TRUE(map_range(tables, kIpaBase, pa_base, k2MiB, desc::kAttrNormalRwx));

  EXPECT_EQ(descriptor_type(l2[128]), desc::kTypeTable);
  EXPECT_EQ(tables.l3_used, 1U);
  EXPECT_EQ(output_addr(pool[0][0]), pa_base);
  EXPECT_EQ(output_addr(pool[0][511]), pa_base + (511U * k4KiB));
}

TEST_F(IdentityMapFixture, PaBelowIpaTranslatesDownward) {
  // Negative displacement (pa < ipa) must work via modular arithmetic.
  const std::uint64_t pa_base = kIpaBase - (2 * k2MiB);
  init_tables(tables);
  ASSERT_TRUE(map_range(tables, kIpaBase, pa_base, k4KiB, desc::kAttrNormalRwx));

  EXPECT_EQ(output_addr(pool[0][0]), pa_base);
}

// ---------------------------------------------------------------------------
// Failure modes — builder must refuse, not corrupt
// ---------------------------------------------------------------------------

TEST_F(IdentityMapFixture, RejectsUnalignedBaseAndSizeAndZero) {
  EXPECT_FALSE(build(kIpaBase + 1, kIpaSize));
  EXPECT_FALSE(build(kIpaBase, kIpaSize + 1));
  EXPECT_FALSE(build(kIpaBase, 0));
}

TEST_F(IdentityMapFixture, RejectsUnalignedPa) {
  init_tables(tables);
  EXPECT_FALSE(map_range(tables, kIpaBase, kIpaBase + 1, kIpaSize, desc::kAttrNormalRwx));
}

TEST_F(IdentityMapFixture, RejectsRangeCrossing1GiB) {
  // 0x7FF0_0000 + 2 MiB crosses the 2 GiB line (L1 index 1 → 2).
  EXPECT_FALSE(build(0x7FF0'0000ULL, k2MiB));
}

TEST_F(IdentityMapFixture, RejectsSecond1GiBRegion) {
  init_tables(tables);
  ASSERT_TRUE(map_identity_range(tables, kIpaBase, kIpaSize, desc::kAttrNormalRwx));
  // 0x8000_0000 lives in L1 slot 2 — a second region needs a second L2.
  EXPECT_FALSE(map_identity_range(tables, 0x8000'0000ULL, kIpaSize, desc::kAttrNormalRwx));
}

TEST_F(IdentityMapFixture, RejectsPoolExhaustion) {
  init_tables(tables);
  // Five distinct sub-2 MiB fragments need five L3 tables; pool has four.
  for (std::size_t i = 0; i < kPoolSize; ++i) {
    ASSERT_TRUE(map_identity_range(tables, kIpaBase + (i * k2MiB), k4KiB, desc::kAttrNormalRwx));
  }
  EXPECT_FALSE(map_identity_range(tables, kIpaBase + (kPoolSize * k2MiB), k4KiB, desc::kAttrNormalRwx));
}

TEST_F(IdentityMapFixture, RejectsPageOverlapOnExistingBlock) {
  init_tables(tables);
  ASSERT_TRUE(map_identity_range(tables, kIpaBase, k2MiB, desc::kAttrNormalRwx));
  EXPECT_FALSE(map_identity_range(tables, kIpaBase + k4KiB, k4KiB, desc::kAttrNormalRwData));
}
