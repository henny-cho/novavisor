#include "nova/abi/guest_layout.h"
#include "smmu/dma_table_model.hpp"

#include <array>
#include <gtest/gtest.h>

namespace {

using namespace nova;

struct TestTables {
  mmu::Table                   l1{};
  std::array<mmu::Table, 1>    l2_pool{};
  std::array<std::uint64_t, 1> l2_pas{{0x4000}};
  std::array<mmu::Table, 2>    l3{};
  std::array<std::uint64_t, 2> l3_pas{{0x8000, 0x9000}};

  [[nodiscard]] auto view() noexcept -> mmu::Stage2Tables {
    return {
        .l1          = &l1,
        .l2_pool     = l2_pool,
        .l2_pool_pas = l2_pas,
        .l3_pool     = l3,
        .l3_pool_pas = l3_pas,
    };
  }
};

// The guest's own RAM, and nothing beside it. The CPU's Stage 2 also
// maps the IVC shared page and the assigned device's register window; a
// device walking these tables would gain DMA access to both, so their
// absence is the whole reason the DMA table set is built separately from
// the CPU's. Both halves are asserted: a builder that mapped nothing at
// all would satisfy the absences on its own.
TEST(SmmuDmaTable, MapsOnlyGuestRam) {
  TestTables                tables{};
  auto                      view = tables.view();
  constexpr GuestDescriptor guest{
      .ipa_base = 0x5000'0000,
      .ipa_size = 0x10'0000,
      .load_pa  = 0x5200'0000,
      .vmid     = 1,
  };

  ASSERT_TRUE(smmu::build_dma_table(view, guest));

  // The window is under a block, so it is reached through one L3 table:
  // L1 slot, L2 slot, then a page per 4 KiB.
  EXPECT_EQ(view.l3_used, 1U);
  EXPECT_EQ(mmu::descriptor_type(tables.l1[mmu::l1_index(guest.ipa_base)]), mmu::desc::kTypeTable);
  EXPECT_EQ(mmu::descriptor_type(tables.l2_pool[0][mmu::l2_index(guest.ipa_base)]), mmu::desc::kTypeTable);

  // Both ends of the guest window, translated to the PA slot backing it.
  // A device that reached the tables but found the wrong output address
  // would read another guest's memory with this guest's StreamID.
  constexpr std::uint64_t kLastPage = guest.ipa_base + guest.ipa_size - mmu::k4KiB;
  const std::uint64_t     first     = tables.l3[0][mmu::l3_index(guest.ipa_base)];
  const std::uint64_t     last      = tables.l3[0][mmu::l3_index(kLastPage)];
  EXPECT_EQ(mmu::descriptor_type(first), mmu::desc::kTypePage);
  EXPECT_EQ(mmu::output_addr(first), guest.load_pa);
  EXPECT_EQ(mmu::descriptor_type(last), mmu::desc::kTypePage);
  EXPECT_EQ(mmu::output_addr(last), guest.to_pa(kLastPage));
  // Data, not code: a device fetches nothing, and the CPU's executable
  // mapping of the same RAM is the CPU's alone.
  EXPECT_TRUE(mmu::execute_never(first));

  // And it stops where the window does — one page further is absent.
  EXPECT_FALSE(mmu::is_valid(tables.l3[0][mmu::l3_index(guest.ipa_base + guest.ipa_size)]));

  EXPECT_FALSE(mmu::is_valid(tables.l2_pool[0][mmu::l2_index(NOVA_IVC_SHM_IPA)]));
  // The assigned device's BAR: not merely unmapped, the whole 1 GiB slot
  // that would hold it was never given a table.
  EXPECT_FALSE(mmu::is_valid(tables.l1[mmu::l1_index(NOVA_EDU_BAR0_IPA)]));
}

} // namespace
