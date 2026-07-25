#pragma once

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

  // Runs after dma_device (it drives a configured device) and before
  // the boot banner (the demo harness expects probe output first). The
  // project nexus places it accordingly.
  constexpr static auto config =
      cib::config(cib::extend<cib::RuntimeStart>(*INIT), cib::extend<HvcService>(&dma_probe_component::handle_hvc));
};

} // namespace nova
