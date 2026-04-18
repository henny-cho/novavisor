// tests/host/stage2_descriptor_test.cpp
//
// Host-side GTest for Stage 2 page table descriptor encoding
// (components/core_mmu/include/stage2_descriptor.hpp).
//
// These tests lock the bit layout of Stage 2 descriptors. The cross-compiled
// hypervisor uses the same header to program VTTBR_EL2-backed tables, so any
// regression here would corrupt guest memory translation in silent ways.

#include "components/core_mmu/include/stage2_descriptor.hpp"

#include <gtest/gtest.h>

using namespace nova::mmu;

// ---------------------------------------------------------------------------
// Constant layout — verify individual bit positions per ARM ARM §D5.3.3
// ---------------------------------------------------------------------------

TEST(Stage2Desc, TypeConstants) {
  EXPECT_EQ(desc::kTypeInvalid, 0b00ULL);
  EXPECT_EQ(desc::kTypeBlock, 0b01ULL);
  EXPECT_EQ(desc::kTypeTable, 0b11ULL);
  EXPECT_EQ(desc::kTypePage, 0b11ULL); // same encoding as Table, level-disambiguated
}

TEST(Stage2Desc, MemAttrPositions) {
  EXPECT_EQ(desc::kMemAttrShift, 2U);
  EXPECT_EQ(desc::kMemAttrMask, 0b111100ULL);
}

TEST(Stage2Desc, S2apPositions) {
  EXPECT_EQ(desc::kS2apShift, 6U);
  EXPECT_EQ(desc::kS2apMask, 0b11000000ULL);
  EXPECT_EQ(desc::kS2apReadWrite, 0b11ULL);
}

TEST(Stage2Desc, SharabilityPositions) {
  EXPECT_EQ(desc::kShShift, 8U);
  EXPECT_EQ(desc::kShInnerShareable, 0b11ULL);
}

TEST(Stage2Desc, AfBitPosition) {
  EXPECT_EQ(desc::kAfBit, 1ULL << 10U);
}

TEST(Stage2Desc, XnBitPosition) {
  // XN is bit 54 in the upper-attributes block.
  EXPECT_EQ(desc::kXnBit, 1ULL << 54U);
}

TEST(Stage2Desc, OutputAddrMaskCoversBits47To12) {
  EXPECT_EQ(desc::kOutputAddrMask, 0x0000'FFFF'FFFF'F000ULL);
  // Ensure bits 11:0 are masked out (low-page offset is not part of frame ID).
  EXPECT_EQ(desc::kOutputAddrMask & 0xFFFULL, 0ULL);
  // Ensure bits 63:48 are masked out (upper-attributes + reserved).
  EXPECT_EQ(desc::kOutputAddrMask & 0xFFFF'0000'0000'0000ULL, 0ULL);
}

// ---------------------------------------------------------------------------
// Attribute presets — expected combined bitfields
// ---------------------------------------------------------------------------

TEST(Stage2Desc, NormalRwxPreset) {
  // MemAttr=0xF (WB), S2AP=RW, SH=InnerSh, AF=1, XN=0
  const std::uint64_t expected = (0xFULL << 2) | (0b11ULL << 6) | (0b11ULL << 8) | (1ULL << 10);
  EXPECT_EQ(desc::kAttrNormalRwx, expected);
  EXPECT_EQ(mem_attr(desc::kAttrNormalRwx), 0xFULL);
  EXPECT_EQ(s2ap(desc::kAttrNormalRwx), desc::kS2apReadWrite);
  EXPECT_EQ(shareability(desc::kAttrNormalRwx), desc::kShInnerShareable);
  EXPECT_TRUE(access_flag(desc::kAttrNormalRwx));
  EXPECT_FALSE(execute_never(desc::kAttrNormalRwx));
}

TEST(Stage2Desc, NormalRwDataPresetIsXn) {
  EXPECT_TRUE(execute_never(desc::kAttrNormalRwData));
  // Same lower attrs as Rwx otherwise.
  const std::uint64_t diff = desc::kAttrNormalRwData ^ desc::kAttrNormalRwx;
  EXPECT_EQ(diff, desc::kXnBit);
}

TEST(Stage2Desc, DeviceRwPreset) {
  EXPECT_EQ(mem_attr(desc::kAttrDeviceRw), desc::kMemAttrDevice_nGnRE);
  EXPECT_EQ(s2ap(desc::kAttrDeviceRw), desc::kS2apReadWrite);
  EXPECT_EQ(shareability(desc::kAttrDeviceRw), desc::kShOuterShareable);
  EXPECT_TRUE(access_flag(desc::kAttrDeviceRw));
  EXPECT_TRUE(execute_never(desc::kAttrDeviceRw));
}

// ---------------------------------------------------------------------------
// Builders — descriptor encoding / round-trip
// ---------------------------------------------------------------------------

TEST(Stage2Desc, MakeBlockRoundTrips) {
  constexpr std::uint64_t pa = 0x0000'0000'5000'0000ULL; // 2MiB-aligned sample PA
  const std::uint64_t     d  = make_block(pa, desc::kAttrNormalRwx);

  EXPECT_EQ(descriptor_type(d), desc::kTypeBlock);
  EXPECT_TRUE(is_valid(d));
  EXPECT_EQ(output_addr(d), pa);
  EXPECT_EQ(mem_attr(d), desc::kMemAttrNormalWB);
  EXPECT_EQ(s2ap(d), desc::kS2apReadWrite);
}

TEST(Stage2Desc, MakePageRoundTrips) {
  constexpr std::uint64_t pa = 0x0000'0000'5000'1000ULL; // 4KiB-aligned
  const std::uint64_t     d  = make_page(pa, desc::kAttrNormalRwData);

  EXPECT_EQ(descriptor_type(d), desc::kTypePage); // same bit pattern as Table, level-disambiguated
  EXPECT_TRUE(is_valid(d));
  EXPECT_EQ(output_addr(d), pa);
  EXPECT_TRUE(execute_never(d));
}

TEST(Stage2Desc, MakeTableCarriesNoAttrs) {
  constexpr std::uint64_t next = 0x0000'0000'0100'0000ULL;
  const std::uint64_t     d    = make_table(next);

  EXPECT_EQ(descriptor_type(d), desc::kTypeTable);
  EXPECT_EQ(output_addr(d), next);
  EXPECT_EQ(mem_attr(d), 0U);
  EXPECT_EQ(s2ap(d), 0U);
  EXPECT_FALSE(access_flag(d));
}

TEST(Stage2Desc, BuildersMaskNonFrameBitsFromPa) {
  // Caller passes a PA with garbage in low 12 bits; builder must mask them.
  const std::uint64_t dirty_pa = 0x0000'0000'5000'0ABCULL;
  const std::uint64_t d        = make_block(dirty_pa, desc::kAttrNormalRwx);
  EXPECT_EQ(output_addr(d), 0x0000'0000'5000'0000ULL);
}

TEST(Stage2Desc, InvalidDescriptorIsZero) {
  EXPECT_EQ(kInvalid, 0ULL);
  EXPECT_FALSE(is_valid(kInvalid));
  EXPECT_EQ(descriptor_type(kInvalid), desc::kTypeInvalid);
}

// ---------------------------------------------------------------------------
// Sanity: presets never collide with type or output-addr fields
// ---------------------------------------------------------------------------

TEST(Stage2Desc, AttrsDoNotEscapeLowerAttrsRegion) {
  // All preset attrs must live in bits 11:2 (lower attrs) plus bit 54 (XN)
  // plus nothing else. Any bit leaking into bits 1:0 or 47:12 would corrupt
  // type or output address encoding.
  constexpr std::uint64_t allowed_bits = (0x3FFULL << 2)     // bits 11:2
                                         | desc::kXnBit      // bit 54
                                         | desc::kContigBit; // bit 52 (not yet used but reserved)

  for (std::uint64_t attr : {desc::kAttrNormalRwx, desc::kAttrNormalRwData, desc::kAttrDeviceRw}) {
    EXPECT_EQ(attr & ~allowed_bits, 0ULL) << "attribute preset leaks into reserved bits: 0x" << std::hex << attr;
    EXPECT_EQ(attr & desc::kTypeMask, 0ULL) << "attribute preset leaks into type field: 0x" << std::hex << attr;
    EXPECT_EQ(attr & desc::kOutputAddrMask, 0ULL)
        << "attribute preset leaks into output-addr field: 0x" << std::hex << attr;
  }
}
