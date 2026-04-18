// tests/host/stage2_builder_test.cpp
//
// Host GTest for Stage 2 identity-map builder
// (components/core_mmu/include/stage2_builder.hpp).
//
// Verifies the exact L1/L2/L3 descriptor layout produced for the Phase 5
// single-guest scenario (IPA 0x5000_0000, 1 MiB), plus boundary behaviors.

#include "components/core_mmu/include/stage2_builder.hpp"

#include <gtest/gtest.h>

using namespace nova::mmu;

namespace {

// Arbitrary distinct non-zero PAs for the two non-root tables. The
// hypervisor uses real link-time addresses; the builder treats these
// as opaque frame identifiers.
constexpr std::uint64_t kFakeL2Pa = 0x0000'0000'0001'0000ULL;
constexpr std::uint64_t kFakeL3Pa = 0x0000'0000'0002'0000ULL;

// Phase 5 guest layout.
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
// build_identity_map — Phase 5 exact scenario
// ---------------------------------------------------------------------------

class IdentityMapFixture : public ::testing::Test {
protected:
  Table        l1{};
  Table        l2{};
  Table        l3{};
  Stage2Tables tables{&l1, &l2, &l3, kFakeL2Pa, kFakeL3Pa};

  void build() { build_identity_map(tables, kIpaBase, kIpaSize, desc::kAttrNormalRwx); }
};

TEST_F(IdentityMapFixture, L1HasOnlyOneValidTableEntry) {
  build();

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
  build();

  EXPECT_EQ(descriptor_type(l2[128]), desc::kTypeTable);
  EXPECT_EQ(output_addr(l2[128]), kFakeL3Pa);
  EXPECT_TRUE(is_valid(l2[128]));

  for (std::size_t i = 0; i < kTableEntries; ++i) {
    if (i == 128) {
      continue;
    }
    EXPECT_EQ(l2[i], kInvalid) << "L2[" << i << "] should be invalid";
  }
}

TEST_F(IdentityMapFixture, L3HasTwoHundredFiftySixIdentityPages) {
  build();

  // 256 pages [0..255] map IPA → PA identity, carry the leaf attrs.
  for (std::size_t i = 0; i < 256; ++i) {
    const std::uint64_t expected_pa = kIpaBase + (static_cast<std::uint64_t>(i) * k4KiB);
    SCOPED_TRACE("L3 page " + std::to_string(i));
    EXPECT_EQ(descriptor_type(l3[i]), desc::kTypePage);
    EXPECT_EQ(output_addr(l3[i]), expected_pa);
    EXPECT_TRUE(access_flag(l3[i]));
    EXPECT_FALSE(execute_never(l3[i])); // Rwx preset
    EXPECT_EQ(mem_attr(l3[i]), desc::kMemAttrNormalWB);
    EXPECT_EQ(s2ap(l3[i]), desc::kS2apReadWrite);
  }

  // Entries 256..511 are invalid (beyond the 1 MiB window).
  for (std::size_t i = 256; i < kTableEntries; ++i) {
    EXPECT_EQ(l3[i], kInvalid) << "L3[" << i << "] should be invalid";
  }
}

// ---------------------------------------------------------------------------
// Builder robustness
// ---------------------------------------------------------------------------

TEST(Stage2Builder, ZerosTablesBeforePopulating) {
  Table l1;
  Table l2;
  Table l3;
  l1.fill(0xDEAD'BEEF'DEAD'BEEFULL);
  l2.fill(0xDEAD'BEEF'DEAD'BEEFULL);
  l3.fill(0xDEAD'BEEF'DEAD'BEEFULL);

  Stage2Tables tables{&l1, &l2, &l3, kFakeL2Pa, kFakeL3Pa};
  build_identity_map(tables, kIpaBase, kIpaSize, desc::kAttrNormalRwx);

  // Spot check: entries that must be invalid really are zero, not
  // leftover sentinel.
  EXPECT_EQ(l1[0], 0ULL);
  EXPECT_EQ(l2[0], 0ULL);
  EXPECT_EQ(l3[256], 0ULL);
  EXPECT_EQ(l3[511], 0ULL);
}

TEST(Stage2Builder, AcceptsXnDataAttrs) {
  Table        l1{};
  Table        l2{};
  Table        l3{};
  Stage2Tables tables{&l1, &l2, &l3, kFakeL2Pa, kFakeL3Pa};

  // Map a single 4 KiB page with XN data attributes.
  build_identity_map(tables, kIpaBase, k4KiB, desc::kAttrNormalRwData);

  EXPECT_TRUE(execute_never(l3[0]));
  EXPECT_EQ(output_addr(l3[0]), kIpaBase);
  EXPECT_EQ(l3[1], kInvalid); // only one page mapped
}

TEST(Stage2Builder, HandlesMaximum2MibRange) {
  Table        l1{};
  Table        l2{};
  Table        l3{};
  Stage2Tables tables{&l1, &l2, &l3, kFakeL2Pa, kFakeL3Pa};

  // 2 MiB = 512 pages — exactly fills L3.
  build_identity_map(tables, kIpaBase, k2MiB, desc::kAttrNormalRwx);

  EXPECT_TRUE(is_valid(l3[0]));
  EXPECT_TRUE(is_valid(l3[511]));
  EXPECT_EQ(output_addr(l3[0]), kIpaBase);
  EXPECT_EQ(output_addr(l3[511]), kIpaBase + (511U * k4KiB));
}
