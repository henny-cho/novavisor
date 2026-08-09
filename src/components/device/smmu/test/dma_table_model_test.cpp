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

// What a device must NOT reach. The CPU's Stage 2 also maps the IVC
// shared page and the control region; a device walking these tables
// would gain DMA access to both, so their absence is the whole reason
// the DMA table set is built separately from the CPU's.
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
  EXPECT_FALSE(mmu::is_valid(tables.l2_pool[0][mmu::l2_index(NOVA_IVC_SHM_IPA)]));
  constexpr std::uint64_t kUnmappedControlPa = 0x60100000;
  EXPECT_FALSE(mmu::is_valid(tables.l2_pool[0][mmu::l2_index(kUnmappedControlPa)]));
}

} // namespace
