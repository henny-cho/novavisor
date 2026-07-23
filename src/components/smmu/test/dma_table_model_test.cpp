#include "nova/abi/guest_layout.h"
#include "smmu/dma_table_model.hpp"

#include <array>
#include <gtest/gtest.h>

namespace {

using namespace nova;

struct TestTables {
  mmu::Table                   l1{};
  mmu::Table                   l2{};
  std::array<mmu::Table, 2>    l3{};
  std::array<std::uint64_t, 2> l3_pas{{0x8000, 0x9000}};

  [[nodiscard]] auto view() noexcept -> mmu::Stage2Tables {
    return {
        .l1          = &l1,
        .l2          = &l2,
        .l2_pa       = 0x4000,
        .l3_pool     = l3,
        .l3_pool_pas = l3_pas,
    };
  }
};

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
  EXPECT_EQ(view.l3_used, 1);
  EXPECT_TRUE(mmu::is_valid(tables.l1[mmu::l1_index(guest.ipa_base)]));
  EXPECT_TRUE(mmu::is_valid(tables.l3[0][mmu::l3_index(guest.ipa_base)]));
  EXPECT_EQ(mmu::output_addr(tables.l3[0][mmu::l3_index(guest.ipa_base)]), guest.load_pa);
  EXPECT_FALSE(mmu::is_valid(tables.l2[mmu::l2_index(NOVA_IVC_SHM_IPA)]));
  EXPECT_FALSE(mmu::is_valid(tables.l2[mmu::l2_index(NOVA_GUEST_PRISTINE_PA)]));
}

TEST(SmmuDmaTable, UsesBlocksForAlignedRam) {
  TestTables                tables{};
  auto                      view = tables.view();
  constexpr GuestDescriptor guest{
      .ipa_base = 0x5000'0000,
      .ipa_size = 0x0800'0000,
      .load_pa  = 0x5800'0000,
      .vmid     = 2,
  };

  ASSERT_TRUE(smmu::build_dma_table(view, guest));
  EXPECT_EQ(view.l3_used, 0);
  for (std::uint64_t ipa = guest.ipa_base; ipa < guest.ipa_base + guest.ipa_size; ipa += mmu::k2MiB) {
    const std::uint64_t descriptor = tables.l2[mmu::l2_index(ipa)];
    EXPECT_EQ(mmu::descriptor_type(descriptor), mmu::desc::kTypeBlock);
    EXPECT_EQ(mmu::output_addr(descriptor), guest.load_pa + ipa - guest.ipa_base);
  }
}

} // namespace
