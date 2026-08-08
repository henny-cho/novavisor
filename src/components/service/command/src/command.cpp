// components/command/src/command.cpp

#include "command/command.hpp"

#include "hal/timer.hpp"
#include "nova/abi/trace_ring.h"
#include "nova/arch/timebase.hpp"
#include "nova/arch/trap_context.hpp"
#include "soft_timer/soft_timer.hpp"
#include "trace/trace.hpp"

#include <cstdint>

namespace nova::command {
namespace {

// The period in counter ticks, converted once at init: CNTFRQ is fixed
// for the life of the machine and this slot re-arms a hundred times a
// second.
std::uint64_t g_period_ticks = 0;

void on_tick(TrapContext* ctx, std::uint64_t arg) noexcept;

void arm() noexcept {
  soft_timer::arm(soft_timer::kSlotCommand, hyp_timer::now() + g_period_ticks, &on_tick, 0);
}

// Runs in the soft_timer IRQ drain on the primary — the single consumer
// this ring's protocol assumes. Re-arms unconditionally: an empty ring
// is the ordinary case, and a slot that stopped when it found nothing
// would make the declared wait true only while commands were already
// arriving.
void on_tick(TrapContext* /*ctx*/, std::uint64_t /*arg*/) noexcept {
  // By address rather than through a lambda: the catalogue offers
  // execute() as a stop point, and a call the linker can inline leaves
  // no symbol to break on.
  g_ring.drain(&execute);
  arm();
}

} // namespace

void execute(const Record& command) noexcept {
  std::uint64_t result = NOVA_CMD_RESULT_OK;
  switch (command.op) {
  case NOVA_CMD_OP_MARK:
    // No effect by design. The record below is the whole of it: proof
    // the ring runs end to end, and a bracket a reader can put around a
    // stretch of the timeline.
    break;
  default:
    result = NOVA_CMD_RESULT_UNKNOWN;
    break;
  }
  // The answer is a trace record rather than a channel of its own, so a
  // command and the effects it caused land on one axis in one clock, and
  // the timeline, the recording and the replay carry it for free. The
  // two argument words travel unchanged; the opcode and the verdict
  // share the remaining word, both being small.
  trace_emit(NOVA_TRACE_EV_COMMAND, static_cast<std::uint32_t>(command.op | (result << 16)), command.a, command.b);
}

void start() noexcept {
  const auto plan = arch::us_to_ticks(hyp_timer::freq(), kPeriodUs);
  if (!plan.accepted) {
    // An unusable counter rate is the timebase contract's business, not
    // this component's. Leaving the page unpublished says "no commands
    // here", which is true, rather than arming a slot at a period the
    // header would then misdescribe.
    return;
  }
  g_period_ticks = plan.ticks;
  place(kPeriodUs);
  arm();
}

} // namespace nova::command
