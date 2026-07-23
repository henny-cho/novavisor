#pragma once

// DMA-only Stage-2 tables map guest RAM and exclude shared CPU mappings.

#include "core_mmu/stage2_builder.hpp"
#include "core_mmu/stage2_descriptor.hpp"
#include "nova/abi/guest.hpp"

namespace nova::smmu {

[[nodiscard]] inline auto build_dma_table(mmu::Stage2Tables& tables, const GuestDescriptor& guest) noexcept -> bool {
  mmu::init_tables(tables);
  return mmu::map_range(tables, guest.ipa_base, guest.load_pa, guest.ipa_size, mmu::desc::kAttrNormalRwData);
}

} // namespace nova::smmu
