#pragma once

#include "boot_msg/boot_msg.hpp"
#include "core_vcpu/core_vcpu.hpp"
#include "dma_device/dma_device.hpp"
#include "smmu/smmu.hpp"
#include "trap_handler/hvc.hpp"

#include <cib/top.hpp>
#include <cstddef>
#include <cstdint>
#include <flow/flow.hpp>

namespace nova::dma_probe {

void               run() noexcept;
[[nodiscard]] auto inject_runtime_fault(std::size_t vm, std::uint64_t generation) noexcept -> bool;

} // namespace nova::dma_probe

namespace nova {

struct dma_probe_component {
  constexpr static auto INIT = flow::action<"dma_probe">([]() noexcept { dma_probe::run(); });

  static void handle_hvc(HvcCall* call) noexcept;

  constexpr static auto config = cib::config(
      cib::extend<cib::RuntimeStart>(dma_device_component::INIT >> *INIT >> boot_msg_component::PRINT_BOOT_MSG),
      cib::extend<HvcService>(&dma_probe_component::handle_hvc));
};

} // namespace nova
