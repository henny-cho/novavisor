// components/telemetry/src/telemetry.cpp

#include "telemetry/telemetry.hpp"

#include "hal/console.hpp"
#include "hal/timer.hpp"
#include "nova/arch/timebase.hpp"
#include "nova/arch/trap_context.hpp"
#include "soft_timer/soft_timer.hpp"

#include <atomic>
#include <cstdint>

namespace nova::telemetry {
namespace {

// The period in counter ticks, converted once at init: CNTFRQ is fixed
// for the machine's life and this slot re-arms fifty times a second.
std::uint64_t g_period_ticks = 0;

// The instant of the last turn. Written by the primary in the drain,
// read by every core that keeps a shadow current — relaxed on both
// sides, because a core seeing the previous turn refreshes one turn
// later rather than wrongly.
std::atomic<std::uint64_t> g_last_turn{0};

void on_tick(TrapContext* ctx, std::uint64_t arg) noexcept;

void arm() noexcept {
  soft_timer::arm(soft_timer::kSlotTelemetry, hyp_timer::now() + g_period_ticks, &on_tick, 0);
}

// Runs in the soft_timer IRQ drain on the primary. The stamp is read
// once and given to every slot this turn touches, so the whole turn
// names one instant rather than a spread of them — the spread is
// between turns, which is what the period already declares.
void on_tick(TrapContext* /*ctx*/, std::uint64_t /*arg*/) noexcept {
  const std::uint64_t stamp = hyp_timer::now_relaxed();
  static_cast<void>(g_publisher.publish(stamp));
  g_last_turn.store(stamp, std::memory_order_relaxed);
  arm();
}

} // namespace

auto last_turn() noexcept -> std::uint64_t {
  return g_last_turn.load(std::memory_order_relaxed);
}

void start() noexcept {
  const auto plan = arch::us_to_ticks(hyp_timer::freq(), kPeriodUs);
  if (!plan.accepted) {
    // An unusable counter rate is the timebase contract's business.
    // Leaving the region unopened says "nothing is published here",
    // which is true, rather than arming a turn whose header would
    // misdescribe how often it comes.
    return;
  }
  g_period_ticks = plan.ticks;
  g_publisher.bind(g_region.byte.data(), kPeriodUs, kBudgetBytes, static_cast<std::uint32_t>(hyp_timer::freq()));

  TelemetryCall call{.publisher = &g_publisher};
  // soft_timer first and by hand — see the header for why it cannot
  // subscribe. Everything else offers itself.
  for (const auto& span : soft_timer::telemetry_spans()) {
    call.declare(span.at, span.bytes);
  }
  cib::service<TelemetryService>(&call);
  g_publisher.open();

  console::line("[telemetry] ", console::Dec{static_cast<std::uint32_t>(g_publisher.slots())}, " slots, ",
                console::Dec{static_cast<std::uint32_t>(g_publisher.bytes())}, " B every ",
                console::Dec{kPeriodUs / 1000}, " ms\n");
  if (call.refused != 0) {
    // Not fatal: what is published is still true, and a machine that
    // refused to boot over an observation would be the observation
    // costing more than it is worth. Loud, because the alternative is a
    // panel that is empty for a reason nobody can see.
    console::line("[telemetry] ", console::Dec{call.refused}, " spans did not fit and are not published\n");
  }
  arm();
}

} // namespace nova::telemetry
