#pragma once

// components/smp/include/smp/dma_quiesce_model.hpp
//
// Pure vocabulary and outcome rule for the DMA half of VM power,
// host-testable. The dispatch that carries it lives in
// smp/dma_quiesce.hpp, which needs cib; this header must not, so the
// rule can be tested on the host.

#include <cstdint>

namespace nova {

enum class DmaQuiesceOp : std::uint8_t {
  kBegin,    // stop new requests and start draining this VM's device
  kPoll,     // has the drain finished?
  kResume,   // attach `generation`'s Stage-2 context before requests resume
  kCanStart, // is the device stack ready for this VM to boot?
};

// kComplete: nothing outstanding, or the action is permitted.
// kPending:  the drain needs more time (kBegin/kPoll only).
// kFailed:   the device could not be quiesced, resumed, or is unsafe.
enum class DmaQuiesceResult : std::uint8_t {
  kComplete,
  kPending,
  kFailed,
};

// An unclaimed request is not a failure: it means the composition has no
// DMA-capable device, so there is nothing to drain, attach or wait for.
// Returning kFailed here would deny VM power to every profile without a
// device stack; returning kPending would hang the reset forever.
[[nodiscard]] constexpr auto dma_quiesce_outcome(bool handled, DmaQuiesceResult result) noexcept -> DmaQuiesceResult {
  return handled ? result : DmaQuiesceResult::kComplete;
}

// The whole truth table. The unclaimed row answers kComplete whatever
// the untouched result carried; the claimed row hands the subscriber's
// verdict through unchanged, negative ones included.
static_assert(
    [] {
      using Result = DmaQuiesceResult;
      return dma_quiesce_outcome(false, Result::kComplete) == Result::kComplete &&
             dma_quiesce_outcome(false, Result::kPending) == Result::kComplete &&
             dma_quiesce_outcome(false, Result::kFailed) == Result::kComplete &&
             dma_quiesce_outcome(true, Result::kComplete) == Result::kComplete &&
             dma_quiesce_outcome(true, Result::kPending) == Result::kPending && // a drain still running
             dma_quiesce_outcome(true, Result::kFailed) == Result::kFailed;     // a device that would not stop
    }(),
    "an unclaimed quiesce is already satisfied, and a claimed one keeps the subscriber's verdict");

} // namespace nova
