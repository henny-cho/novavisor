// components/command/src/command.cpp

#include "command/command.hpp"

#include "core_vcpu/core_vcpu.hpp"
#include "hal/timer.hpp"
#include "nova/abi/trace_ring.h"
#include "nova/arch/timebase.hpp"
#include "nova/arch/trap_context.hpp"
#include "soft_timer/soft_timer.hpp"
#include "trace/trace.hpp"
#include "vgic/vgic.hpp"

#include <cstdint>
#include <limits>

namespace nova::command {
namespace {

// The period in counter ticks, converted once at init: CNTFRQ is fixed
// for the machine's life and this slot re-arms a hundred times a second.
std::uint64_t g_period_ticks = 0;

// Carry out one command. Every argument is checked here, before it is
// narrowed to the width the callee takes: an INTID of 2^32 + 30 passes
// a range test on the truncated value and posts an interrupt nobody
// asked for.
auto run(const Record& command) noexcept -> std::uint64_t {
  switch (command.op) {
  case NOVA_CMD_OP_MARK:
    // No effect by design: the record is the whole of it — proof the
    // ring runs end to end, and a bracket around a stretch of timeline.
    return NOVA_CMD_RESULT_OK;
  case NOVA_CMD_OP_SPI:
    if (command.b > std::numeric_limits<std::uint32_t>::max()) {
      return NOVA_CMD_RESULT_RANGE;
    }
    if (!vcpu::vm_on(command.a)) {
      // Well formed, nothing to deliver it to. Posting anyway leaves a
      // pending bit for whatever boots into that slot next.
      return NOVA_CMD_RESULT_STATE;
    }
    return vgic::post_spi(command.a, static_cast<std::uint32_t>(command.b)) ? NOVA_CMD_RESULT_OK
                                                                            : NOVA_CMD_RESULT_RANGE;
  case NOVA_CMD_OP_SLICE:
    return vcpu::set_slice_us(command.a) ? NOVA_CMD_RESULT_OK : NOVA_CMD_RESULT_RANGE;
  default:
    return NOVA_CMD_RESULT_UNKNOWN;
  }
}

void on_tick(TrapContext* ctx, std::uint64_t arg) noexcept;

void arm() noexcept {
  soft_timer::arm(soft_timer::kSlotCommand, hyp_timer::now() + g_period_ticks, &on_tick, 0);
}

// Runs in the soft_timer IRQ drain on the primary — the single consumer
// this ring's protocol assumes. Re-arms unconditionally: an empty ring
// is the ordinary case, and a slot that stopped on finding nothing
// would make the declared wait true only once commands were arriving.
void on_tick(TrapContext* /*ctx*/, std::uint64_t /*arg*/) noexcept {
  // By address, not through a lambda: taking it keeps the stop-point
  // symbol from being inlined away and collected with --gc-sections.
  g_ring.drain(&execute);
  arm();
}

} // namespace

void execute(const Record& command) noexcept {
  const std::uint64_t result = run(command);
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
  // The bands come from the components that enforce them, so what the
  // page advertises and what run() accepts cannot drift apart.
  const auto slice = vcpu::slice_band();
  const auto spi   = vgic::spi_band();
  place(kPeriodUs, {.slice_min_us = slice.min_us,
                    .slice_def_us = slice.default_us,
                    .slice_max_us = slice.max_us,
                    .spi_lo       = spi.lo,
                    .spi_hi       = spi.hi});
  arm();
}

} // namespace nova::command
