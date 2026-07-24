#pragma once

#include "board.hpp"
#include "hal/board/common/gicv3.hpp"

#include <cstdint>

namespace nova::board::qemu_virt {

struct GicConfig {
  inline static constexpr std::uintptr_t kDistributorBase   = GICD_BASE;
  inline static constexpr std::uintptr_t kRedistributorBase = GICR_BASE;
  inline static constexpr auto           kCpuAffinity       = board::qemu_virt::kCpuAffinity;
};

static_assert(GicConfig::kCpuAffinity.size() == kSmpCpus);
using Gicv3 = common::Gicv3<GicConfig>;

} // namespace nova::board::qemu_virt
