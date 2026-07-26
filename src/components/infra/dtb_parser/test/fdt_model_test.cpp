// Host tests for the FDT walker: header validation, node/prop
// primitives and the guest-config extraction, driven by blobs the
// real yml2dtb generator produced (fdt_fixture.hpp).

#include "dtb_parser/fdt_model.hpp"
#include "fdt_fixture.hpp"

#include <cstdint>
#include <gtest/gtest.h>
#include <vector>

using namespace nova::fdt;

namespace {

auto large() -> Bytes {
  return {fixtures::kLargeDtb, sizeof(fixtures::kLargeDtb)};
}
auto no_uart() -> Bytes {
  return {fixtures::kNoUartDtb, sizeof(fixtures::kNoUartDtb)};
}
auto mixed() -> Bytes {
  return {fixtures::kMixedDtb, sizeof(fixtures::kMixedDtb)};
}

TEST(FdtView, ValidatesHeaderAndRejectsCorruption) {
  EXPECT_TRUE(make_view(large()).ok);

  std::vector<std::uint8_t> bad(large().begin(), large().end());
  bad[0] ^= 0xFFU; // break the magic
  EXPECT_FALSE(make_view({bad.data(), bad.size()}).ok);

  EXPECT_FALSE(make_view(large().first(39)).ok); // shorter than a header
  EXPECT_FALSE(make_view(large().first(64)).ok); // header ok, body truncated
  EXPECT_FALSE(make_view({}).ok);
}

TEST(FdtNode, FindChildMatchesUnitAddressSuffix) {
  const View v = make_view(large());
  EXPECT_TRUE(find_child(v, kRootNode, "memory").ok); // memory@50000000
  EXPECT_TRUE(find_child(v, kRootNode, "psci").ok);   // exact
  EXPECT_FALSE(find_child(v, kRootNode, "memo").ok);  // no prefix matching without '@'
  EXPECT_FALSE(find_child(v, kRootNode, "missing").ok);
}

TEST(FdtNode, CountChildrenSeesOnlyDirectChildren) {
  const View    v    = make_view(large());
  const NodeRef cpus = find_child(v, kRootNode, "cpus");
  ASSERT_TRUE(cpus.ok);
  EXPECT_EQ(count_children(v, cpus.off, "cpu"), 2U);
  EXPECT_EQ(count_children(v, kRootNode, "cpu"), 0U); // cpu@N are not root children
}

TEST(FdtProp, ReadsCellsAndRejectsMissing) {
  const View    v   = make_view(large());
  const NodeRef mem = find_child(v, kRootNode, "memory");
  ASSERT_TRUE(mem.ok);

  const PropRef reg = find_prop(v, mem.off, "reg");
  ASSERT_TRUE(reg.ok);
  EXPECT_EQ(reg.data.size(), 16U);
  EXPECT_EQ(prop_u64(reg, 0), 0x50000000U);
  EXPECT_EQ(prop_u64(reg, 1), 0x00800000U);
  EXPECT_EQ(prop_u64(reg, 2), 0U); // off the end -> 0

  EXPECT_FALSE(find_prop(v, mem.off, "missing").ok);
  const PropRef cells = find_prop(v, kRootNode, "#address-cells");
  ASSERT_TRUE(cells.ok);
  EXPECT_EQ(prop_u32(cells, 0), 2U);
}

TEST(FdtGuest, ParsesLargeConfig) {
  const GuestInfo info = parse_guest(large());
  ASSERT_TRUE(info.ok);
  EXPECT_EQ(info.mem_base, 0x50000000U);
  EXPECT_EQ(info.mem_size, 0x00800000U);
  EXPECT_EQ(info.cpus, 2U);
  EXPECT_TRUE(info.has_uart);
  // No placement hints in this config — index-derived defaults apply.
  EXPECT_FALSE(info.autostart);
  EXPECT_FALSE(info.has_affinity);
}

TEST(FdtGuest, ParsesMixedPlacementHints) {
  const GuestInfo info = parse_guest(mixed());
  ASSERT_TRUE(info.ok);
  EXPECT_EQ(info.mem_size, 0x08000000U);
  EXPECT_EQ(info.cpus, 1U);
  EXPECT_TRUE(info.autostart);
  ASSERT_TRUE(info.has_affinity);
  EXPECT_EQ(info.affinity[0], 1U);
}

TEST(FdtGuest, ParsesNoUartConfig) {
  const GuestInfo info = parse_guest(no_uart());
  ASSERT_TRUE(info.ok);
  EXPECT_EQ(info.mem_size, 0x00300000U);
  EXPECT_EQ(info.cpus, 1U);
  EXPECT_FALSE(info.has_uart);
}

TEST(FdtGuest, TruncatedBlobFailsClosed) {
  for (std::size_t len : {0U, 39U, 40U, 100U}) {
    if (len < large().size()) {
      EXPECT_FALSE(parse_guest(large().first(len)).ok) << "len=" << len;
    }
  }
}

} // namespace
