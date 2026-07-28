#pragma once

// components/smp/include/smp/dma_quiesce.hpp
//
// The DMA half of the VM power protocol, as a claimed-dispatch contract
// rather than a direct call.
//
// VM power is smp's: a reset must not restore memory while a device can
// still write into it, and a new generation must be attached before
// vcpu 0 becomes runnable. Who performs the drain is a different
// question — it belongs to whatever device stack the composition
// happens to include. Calling dma_device directly answered both at once
// and made guest-facing PSCI drag the SMMU and the DMA device into every
// profile that wanted VM power, so a single-core board profile could not
// serve PSCI without bringing up device isolation it deliberately
// excludes.
//
// Inverted: smp exports the service (the invoker owns it, as core_gic
// owns IrqService), a device component subscribes, and an unsubscribed
// composition means there is no DMA to isolate — every request is
// already satisfied. The vocabulary and that outcome rule are pure and
// host-tested in smp/dma_quiesce_model.hpp.

#include "smp/dma_quiesce_model.hpp"

#include <cib/top.hpp>
#include <cstddef>
#include <cstdint>
#include <nexus/callback.hpp>

namespace nova {

struct DmaQuiesceCall {
  DmaQuiesceOp     op         = DmaQuiesceOp::kBegin;
  std::size_t      vm         = 0;
  std::uint64_t    generation = 0; // kResume only
  DmaQuiesceResult result     = DmaQuiesceResult::kComplete;
  bool             handled    = false;
};

struct DmaQuiesceService : public callback::service<DmaQuiesceCall*> {};

namespace smp {

[[nodiscard]] inline auto dma_quiesce(DmaQuiesceOp op, std::size_t vm, std::uint64_t generation = 0) noexcept
    -> DmaQuiesceResult {
  DmaQuiesceCall call{.op = op, .vm = vm, .generation = generation};
  cib::service<DmaQuiesceService>(&call);
  return dma_quiesce_outcome(call.handled, call.result);
}

[[nodiscard]] inline auto dma_begin_quiesce(std::size_t vm) noexcept -> DmaQuiesceResult {
  return dma_quiesce(DmaQuiesceOp::kBegin, vm);
}

[[nodiscard]] inline auto dma_poll_quiesce(std::size_t vm) noexcept -> DmaQuiesceResult {
  return dma_quiesce(DmaQuiesceOp::kPoll, vm);
}

[[nodiscard]] inline auto dma_resume_vm(std::size_t vm, std::uint64_t generation) noexcept -> bool {
  return dma_quiesce(DmaQuiesceOp::kResume, vm, generation) == DmaQuiesceResult::kComplete;
}

[[nodiscard]] inline auto dma_can_start(std::size_t vm) noexcept -> bool {
  return dma_quiesce(DmaQuiesceOp::kCanStart, vm) == DmaQuiesceResult::kComplete;
}

} // namespace smp
} // namespace nova
