// components/soft_timer/src/soft_timer.cpp
//
// Deadline-slot queue driving CNTHP. Every mutation (arm, cancel,
// expiry drain) ends by reprogramming the hardware one-shot to the
// earliest remaining deadline — CNTHP is level-triggered, so leaving a
// met condition armed would re-fire forever after EOI. A callback that
// arms an already-passed deadline simply retriggers the IRQ.

#include "soft_timer/soft_timer.hpp"

#include "hal/gic.hpp"
#include "hal/timer.hpp"

#include <cstddef>
#include <cstdint>

namespace nova::soft_timer {
namespace {

TimerQueue<kSlotCount> g_queue;

// Program CNTHP to the earliest armed deadline, or park it.
void reprogram() noexcept {
  const std::uint64_t next = g_queue.next_deadline();
  if (next == kNoDeadline) {
    hyp_timer::stop();
  } else {
    hyp_timer::arm_at(next);
  }
}

void drain_expired(TrapContext* ctx) noexcept {
  TimerQueue<kSlotCount>::Expired due;
  while (g_queue.pop_expired(hyp_timer::now(), due)) {
    due.fn(ctx, due.arg);
  }
  reprogram();
}

} // namespace

void init() noexcept {
  gic::enable_ppi(hyp_timer::kHypTimerIntid);
}

void arm(std::size_t slot, std::uint64_t deadline, Callback fn, std::uint64_t arg) noexcept {
  g_queue.arm(slot, deadline, fn, arg);
  reprogram();
}

void cancel(std::size_t slot) noexcept {
  g_queue.cancel(slot);
  reprogram();
}

} // namespace nova::soft_timer

namespace nova {

void soft_timer_component::handle_irq(IrqCall* call) noexcept {
  if (call->intid != hyp_timer::kHypTimerIntid) {
    return; // not ours — leave unclaimed for other subscribers
  }
  call->handled = true;
  soft_timer::drain_expired(call->ctx);
}

} // namespace nova
