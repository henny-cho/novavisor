#pragma once

#include "boot_msg/boot_msg.hpp"
#include "core_gic/core_gic.hpp"
#include "core_mmu/core_mmu.hpp"
#include "core_timer/core_timer.hpp"
#include "core_vcpu/core_vcpu.hpp"
#include "soft_timer/soft_timer.hpp"
#include "trap_handler/trap_handler.hpp"
#include "vgic/vgic.hpp"

#include <cib/top.hpp>

namespace nova {

struct nova_project {
  constexpr static auto config =
      cib::components<core_mmu_component, core_gic_component, vgic_component, core_timer_component,
                      soft_timer_component, boot_msg_component, trap_handler_component, core_vcpu_component>;
};

using nova_top = cib::top<nova_project>;

} // namespace nova
