#pragma once

#include "boot_msg/boot_msg.hpp"
#include "core_vcpu/core_vcpu.hpp"
#include "smmu/smmu.hpp"

#include <cib/top.hpp>
#include <flow/flow.hpp>

namespace nova::dma_probe {

void run() noexcept;

} // namespace nova::dma_probe

namespace nova {

struct dma_probe_component {
  constexpr static auto INIT = flow::action<"dma_probe">([]() noexcept { dma_probe::run(); });

  constexpr static auto config = cib::config(
      cib::extend<cib::RuntimeStart>(core_vcpu_component::INIT >> *INIT >> boot_msg_component::PRINT_BOOT_MSG));
};

} // namespace nova
