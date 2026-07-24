#pragma once

#include "board.hpp"
#include "hal/board/common/smmuv3.hpp"

namespace nova::board::qemu_virt {

using Smmuv3 = common::Smmuv3<SMMU_BASE>;

} // namespace nova::board::qemu_virt
