#pragma once

// Trace Component
//
// Places the T layer's rings at the board's reserved physical address
// and gives the hot paths one line to emit with.
//
// The rings themselves live in nova/trace.hpp as inline storage, so a
// call site takes no link dependency on this component: a profile that
// omits it leaves them unplaced and every emit() drops. Observation is
// never a reason a build fails to link.
//
// Always on. A build switch here would create the failure this layer
// exists to remove — "tracing was off when it happened" — and the cost
// it would save is six stores and a counter read.

#include "hal/cpu.hpp"
#include "hal/timer.hpp"
#include "nova/trace.hpp"

#include <cib/top.hpp>
#include <cstdint>
#include <flow/flow.hpp>

namespace nova {

// The seam where the board's core count meets the region's ring count.
// A core past the last ring would index g_ring out of bounds on every
// emit, so this is not a loss to account for like the pre-placement
// drops — it is a state the build must not be able to reach.
static_assert(cpu::kMaxCpus <= NOVA_TRACE_MAX_RINGS, "this board has more cores than the trace region has rings");

namespace trace_detail {

inline void place() noexcept {
  trace::place(reinterpret_cast<void*>(static_cast<std::uintptr_t>(board::active::kTracePa)), cpu::kMaxCpus,
               static_cast<std::uint32_t>(hyp_timer::freq()));
}

} // namespace trace_detail

// The hot-path entry point. Reads which core it is on rather than being
// told: `cpu::id()` is one MRS and already sits on every path that
// calls this, so passing it in would save nothing and let a caller pass
// the wrong one.
inline void trace_emit(std::uint16_t type, std::uint32_t a, std::uint64_t b = 0, std::uint64_t c = 0) noexcept {
  const std::size_t cpu = cpu::id();
  trace::g_ring[cpu].emit(hyp_timer::now_relaxed(), type, static_cast<std::uint8_t>(cpu), a, b, c);
}

struct trace_component {
  constexpr static auto INIT = flow::action<"trace_init">([]() noexcept { trace_detail::place(); });

  constexpr static auto config = cib::config(cib::extend<cib::RuntimeStart>(*INIT));
};

} // namespace nova
