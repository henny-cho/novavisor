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
#include "nova/abi/guest_layout.h"
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

// The other half of that seam: the region is divided by the core count,
// so a board can no longer declare a capacity that does not fit — it
// can only reserve too little. A floor rather than a ceiling, and the
// only sizing decision left for a port to get wrong.
static_assert(trace::records_per_ring(board::active::kTraceSize, cpu::kMaxCpus) >= NOVA_TRACE_MIN_CAPACITY,
              "this board reserves less trace region than the T layer is worth");

// And the reservation has to be a reservation. The DTB generator checks
// the same thing over a config's placements, but only when an image is
// built for a guest set; this runs for every build. On the other side
// are the pristine images the recovery path re-loads from, so a region
// that grew into them would surface as a guest restarting into rubble.
static_assert(board::active::kTracePa + board::active::kTraceSize <= board::active::kGuestPristinePa,
              "the trace region overruns the pristine guest images");

// And its lower bound, the same rule from the other side: the IVC page
// is the guests' one window into EL2 memory, so a page that reached into
// the rings would let a guest rewrite the history of its own run.
static_assert(board::active::kIvcShmPa + NOVA_IVC_SHM_SIZE <= board::active::kTracePa,
              "the IVC shared page overruns the trace region");

namespace trace_detail {

inline void place() noexcept {
  trace::place(reinterpret_cast<void*>(static_cast<std::uintptr_t>(board::active::kTracePa)), board::active::kTraceSize,
               cpu::kMaxCpus, static_cast<std::uint32_t>(hyp_timer::freq()));
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
