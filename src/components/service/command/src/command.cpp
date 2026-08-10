// components/command/src/command.cpp

#include "command/command.hpp"

#include "hal/console.hpp"
#include "hal/timer.hpp"
#include "nova/abi/trace_ring.h"
#include "nova/arch/timebase.hpp"
#include "nova/arch/trap_context.hpp"
#include "soft_timer/soft_timer.hpp"
#include "trace/trace.hpp"

#include <cib/top.hpp>
#include <cstdint>

namespace nova::command {
namespace {

// The period in counter ticks, converted once at init: CNTFRQ is fixed
// for the machine's life and this slot re-arms a hundred times a second.
std::uint64_t g_period_ticks = 0;

// What this build carries out, collected once at init. Storage lives
// here rather than beside the ring, so a profile composing no command
// component holds no table.
OpTable g_ops{};

// The ring's own opcode: no effect by design. The record is the whole
// of it — proof the ring runs end to end, and a bracket around a
// stretch of timeline.
auto mark(const Record& /*command*/, TrapContext* /*ctx*/) noexcept -> std::uint64_t {
  return NOVA_CMD_RESULT_OK;
}

void on_tick(TrapContext* ctx, std::uint64_t arg) noexcept;

void arm() noexcept {
  soft_timer::arm(soft_timer::kSlotCommand, hyp_timer::now() + g_period_ticks, &on_tick, 0);
}

// Runs in the soft_timer IRQ drain on the primary — the single consumer
// this ring's protocol assumes. Re-arms unconditionally: an empty ring
// is the ordinary case, and a slot that stopped on finding nothing
// would make the declared wait true only once commands were arriving.
void on_tick(TrapContext* ctx, std::uint64_t /*arg*/) noexcept {
  // The frame rides in the closure. Not because a handler is known to
  // need it, but because no layer here should have to know whether one
  // does — core_gic answers that once, for everybody.
  //
  // execute is still reached through its address: taking it keeps the
  // stop-point symbol from being inlined away and collected with
  // --gc-sections, which a bare call inside the closure would not.
  //
  // The tally is dropped on purpose: execute() emits a trace record per
  // command, so how many arrived is already on the timeline and a second
  // count here would be the same fact from a worse vantage point.
  static void (*const kExecute)(const Record&, TrapContext*) noexcept = &execute;
  static_cast<void>(g_ring.drain([ctx](const Record& command) { kExecute(command, ctx); }));
  arm();
}

} // namespace

void execute(const Record& command, TrapContext* ctx) noexcept {
  const std::uint64_t result = g_ops.dispatch(command, ctx);
  // A trace record rather than a channel of its own, so a command and
  // the effects it caused land on one axis in one clock. The two
  // argument words travel unchanged; the opcode and the verdict share
  // the remaining one. An opcode too wide for its half goes out as zero
  // rather than as whatever it truncates to, which would be some other
  // op's name against this one's verdict.
  const std::uint64_t named = command.op <= NOVA_CMD_ANSWER_MASK ? command.op : 0;
  trace_emit(NOVA_TRACE_EV_COMMAND, static_cast<std::uint32_t>(named | (result << NOVA_CMD_ANSWER_SHIFT)), command.a,
             command.b);
}

void start() noexcept {
  const auto plan = arch::us_to_ticks(hyp_timer::freq(), kPeriodUs);
  if (!plan.accepted) {
    // An unusable counter rate is the timebase contract's business.
    // Leaving the page unpublished says "no commands here", which is
    // true, rather than arming a slot the header would misdescribe.
    return;
  }
  g_period_ticks = plan.ticks;

  // Collect, then publish. The page's rows are this table projected, so
  // a row arriving after placement could not be advertised; doing both
  // here makes that order structural rather than a rule about the order
  // component inits run in.
  CommandCall call{.table = &g_ops};
  cib::service<CommandService>(&call);
  if (call.refused != 0) {
    console::line("[command] ", console::Dec{call.refused}, " ops did not fit and are not offered\n");
  }
  place(kPeriodUs, g_ops);
  arm();
}

} // namespace nova::command

namespace nova {

void command_component::commands(CommandCall* call) noexcept {
  call->declare({.op = NOVA_CMD_OP_MARK, .words = 1, .run = &command::mark});
}

} // namespace nova
