#pragma once

#include "board.hpp"
#include "hal/board/common/gicv3.hpp"

#include <cstdint>

namespace nova::board::active {

struct GicConfig {
  inline static constexpr std::uintptr_t kDistributorBase   = kGicdBase;
  inline static constexpr std::uintptr_t kRedistributorBase = kGicrBase;
  inline static constexpr auto           kCpuAffinity       = active::kCpuAffinity;
};

// The affinity table is hand-written while the core count comes from
// board_layout.h; a mismatch would leave a core without a route.
static_assert(GicConfig::kCpuAffinity.size() == kSmpCpus);
using Gicv3 = common::Gicv3<GicConfig>;

} // namespace nova::board::active
