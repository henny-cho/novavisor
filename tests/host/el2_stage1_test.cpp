// tests/host/el2_stage1_test.cpp
//
// EL2 Stage-1 identity-map builder: block-size selection, W^X
// attribute placement, overlap/pool-exhaustion rejection, and the
// translation register values shared with boot.S.

#include "hal/arch/aarch64/vmsa/stage1_tables.hpp"

#include <cstdint>
#include <gtest/gtest.h>
#include <span>

namespace {

using namespace nova::arch::stage1;

constexpr std::uint64_t kGiB = 1ULL << 30;
constexpr std::uint64_t kMiB = 1ULL << 20;

struct Fixture : ::testing::Test {
  Table         root{};
  Table         pool[8]{};
  Stage1Builder builder{root, std::span{pool}};
};

struct Leaf {
  int           level;
  std::uint64_t desc;
};

auto next_table(std::uint64_t d) -> const Table* {
  return reinterpret_cast<const Table*>(static_cast<std::uintptr_t>(d & desc::kOutputAddrMask));
}

// Walk one VA to its leaf, mirroring the hardware level order.
auto walk(const Table& root, std::uint64_t va) -> Leaf {
  const std::uint64_t l1 = root.entry[(va >> 30) % kEntries];
  if ((l1 & desc::kTypeMask) != desc::kTypeTable) {
    return {1, l1};
  }
  const std::uint64_t l2 = next_table(l1)->entry[(va >> 21) % kEntries];
  if ((l2 & desc::kTypeMask) != desc::kTypeTable) {
    return {2, l2};
  }
  return {3, next_table(l2)->entry[(va >> 12) % kEntries]};
}

auto is_writable(std::uint64_t d) -> bool {
  return (d & desc::kApReadOnly) == 0;
}
auto is_executable(std::uint64_t d) -> bool {
  return (d & desc::kXnBit) == 0;
}

// Recursively assert no leaf is both writable and executable.
void assert_wxn(const Table& t, int level) {
  for (const auto d : t.entry) {
    const auto type = d & desc::kTypeMask;
    if (type == desc::kTypeInvalid) {
      continue;
    }
    if (level < 3 && type == desc::kTypeTable) {
      assert_wxn(*next_table(d), level + 1);
      continue;
    }
    EXPECT_FALSE(is_writable(d) && is_executable(d));
  }
}

// QEMU-virt-like geometry: device below RAM, image at the RAM base.
struct QemuLayout {
  std::uint64_t ram_lo     = 0x40000000;
  std::uint64_t text_start = 0x40000000; // boundary coincides with ram_lo
  std::uint64_t text_end   = 0x40080000;
  std::uint64_t rodata_end = 0x400A0000;
  std::uint64_t ram_hi     = 0x80000000;
};

auto map_layout(Stage1Builder& b, const QemuLayout& l) -> bool {
  return b.map(0, l.ram_lo, desc::kAttrDevice) && b.map(l.ram_lo, l.text_start, desc::kAttrNormalRw) &&
         b.map(l.text_start, l.text_end, desc::kAttrNormalRx) && b.map(l.text_end, l.rodata_end, desc::kAttrNormalRo) &&
         b.map(l.rodata_end, l.ram_hi, desc::kAttrNormalRw);
}

TEST_F(Fixture, DeviceGiBUsesLevel1Block) {
  ASSERT_TRUE(map_layout(builder, QemuLayout{}));
  const auto leaf = walk(root, 0x09000000); // UART, inside the device window
  EXPECT_EQ(leaf.level, 1);
  EXPECT_EQ(leaf.desc & desc::kTypeMask, desc::kTypeBlock);
  EXPECT_EQ((leaf.desc & desc::kAttrIndxMask) >> desc::kAttrIndxShift, desc::kAttrIndxDevice);
  EXPECT_FALSE(is_executable(leaf.desc));
  EXPECT_TRUE((leaf.desc & desc::kAfBit) != 0);
}

TEST_F(Fixture, TextIsPageMappedReadOnlyExecutable) {
  ASSERT_TRUE(map_layout(builder, QemuLayout{}));
  const auto leaf = walk(root, 0x40000000);
  EXPECT_EQ(leaf.level, 3);
  EXPECT_EQ(leaf.desc & desc::kOutputAddrMask, 0x40000000U);
  EXPECT_FALSE(is_writable(leaf.desc));
  EXPECT_TRUE(is_executable(leaf.desc));
  EXPECT_EQ((leaf.desc & desc::kShMask) >> desc::kShShift, desc::kShInnerShareable);
}

TEST_F(Fixture, RodataIsReadOnlyNeverExecutable) {
  ASSERT_TRUE(map_layout(builder, QemuLayout{}));
  const auto leaf = walk(root, 0x40090000);
  EXPECT_EQ(leaf.level, 3);
  EXPECT_FALSE(is_writable(leaf.desc));
  EXPECT_FALSE(is_executable(leaf.desc));
}

TEST_F(Fixture, RamInteriorCoalescesToLevel2Blocks) {
  ASSERT_TRUE(map_layout(builder, QemuLayout{}));
  const auto leaf = walk(root, 0x50000000); // guest RAM, far from boundaries
  EXPECT_EQ(leaf.level, 2);
  EXPECT_EQ(leaf.desc & desc::kTypeMask, desc::kTypeBlock);
  EXPECT_TRUE(is_writable(leaf.desc));
  EXPECT_FALSE(is_executable(leaf.desc));
  EXPECT_EQ((leaf.desc & desc::kAttrIndxMask) >> desc::kAttrIndxShift, desc::kAttrIndxNormal);
}

TEST_F(Fixture, WholeTreeSatisfiesWxn) {
  ASSERT_TRUE(map_layout(builder, QemuLayout{}));
  assert_wxn(root, 1);
}

TEST_F(Fixture, FullyAlignedGiBOfRamUsesLevel1Block) {
  // N1SDP-like: a RAM GiB with no image inside stays a single L1 entry.
  ASSERT_TRUE(builder.map(0, 2 * kGiB, desc::kAttrDevice));
  ASSERT_TRUE(builder.map(2 * kGiB, 3 * kGiB, desc::kAttrNormalRw));
  EXPECT_EQ(walk(root, 2 * kGiB + 512 * kMiB).level, 1);
  EXPECT_EQ(builder.tables_used(), 0U);
}

TEST_F(Fixture, EmptyRangeSucceedsWithoutAllocating) {
  EXPECT_TRUE(builder.map(kGiB, kGiB, desc::kAttrNormalRw));
  EXPECT_EQ(builder.tables_used(), 0U);
}

TEST_F(Fixture, RejectsMisalignedAndOverflowingRanges) {
  EXPECT_FALSE(builder.map(0x1000, 0x1800, desc::kAttrNormalRw)); // end misaligned
  EXPECT_FALSE(builder.map(0x800, 0x2000, desc::kAttrNormalRw));  // base misaligned
  EXPECT_FALSE(builder.map(kVaLimit - kGiB, kVaLimit + kGiB, desc::kAttrNormalRw));
  EXPECT_FALSE(builder.map(2 * kGiB, kGiB, desc::kAttrNormalRw)); // inverted
}

TEST_F(Fixture, RejectsOverlapAtEveryGranularity) {
  ASSERT_TRUE(builder.map(0, kGiB, desc::kAttrDevice));
  EXPECT_FALSE(builder.map(0, kGiB, desc::kAttrDevice));                             // same L1 block
  EXPECT_FALSE(builder.map(512 * kMiB, 512 * kMiB + 2 * kMiB, desc::kAttrNormalRw)); // under a block
  ASSERT_TRUE(builder.map(kGiB, kGiB + 4 * kMiB, desc::kAttrNormalRw));
  EXPECT_FALSE(builder.map(kGiB + kMiB, kGiB + 2 * kMiB, desc::kAttrNormalRo)); // page overlap
}

TEST_F(Fixture, FailsCleanlyWhenPoolIsExhausted) {
  Table         tiny_root{};
  Table         tiny_pool[1]{};
  Stage1Builder tiny{tiny_root, std::span{tiny_pool}};
  // Needs one L2 and one L3 table; the pool only holds one.
  EXPECT_FALSE(tiny.map(0, 4 * kPageSize, desc::kAttrNormalRw));
}

TEST(Stage1Regs, ValuesMatchBootConstants) {
  EXPECT_EQ(kMairEl2, static_cast<std::uint64_t>(NOVA_EL2_MAIR));
  EXPECT_EQ(kTcrEl2, static_cast<std::uint64_t>(NOVA_EL2_TCR));
  EXPECT_EQ(kSctlrEl2, static_cast<std::uint64_t>(NOVA_EL2_SCTLR));
  EXPECT_EQ(kTcrEl2 & 0x3FU, 32U);            // T0SZ covers exactly 4 GiB
  EXPECT_TRUE((kSctlrEl2 & (1U << 19)) != 0); // WXN backs the map's W^X
}

} // namespace
