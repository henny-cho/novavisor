#pragma once

// Telemetry Component
//
// Places the S layer's region and takes the machine's own reading of it
// on a period of its own.
//
// Why the machine reads itself. A host cannot ask a running machine to
// hold still, so anything it reads over the top of EL2 is a reading
// smeared across however long that read took. A core taking its own
// copy at one instant, under a sequence that says when the copy is
// whole, is the difference between a sample and a snapshot — and it is
// what lets the S layer be exact without stopping anything, which until
// now only the H layer could offer.
//
// Why components hand their spans over rather than this one reaching
// in. Most of what is worth publishing is TU-private
// (nova::vgic::(anonymous)::g_cpu), so the owning translation unit is
// the only place its address can be taken. Extending the service is
// also what makes the published set follow the composition: a profile
// built without smmu publishes no stream table rather than failing to
// link.
//
// soft_timer is the one exception, for one reason: this component runs
// on a slot of its queue, so it already sits above soft_timer in the
// graph. Subscribing would be a dependency back down, so its spans come
// through a plain function that this component calls.

#include "nova/telemetry.hpp"

#include <cib/top.hpp>
#include <cstddef>
#include <cstdint>
#include <flow/flow.hpp>
#include <nexus/callback.hpp>

namespace nova {

// What a subscriber is handed to register with.
//
// Refusals are counted rather than returned: a component offering four
// spans should not have to decide what to do when the third does not
// fit, and the number that did not fit is one fact the machine reports
// once, at init.
struct TelemetryCall {
  telemetry::Publisher* publisher = nullptr;
  std::uint32_t         refused   = 0;

  void declare(const void* source, std::size_t bytes) noexcept {
    if (publisher == nullptr || !publisher->declare(source, bytes)) {
      ++refused;
    }
  }
};

struct TelemetryService : public callback::service<TelemetryCall*> {};

namespace telemetry {

// How stale a reading may be. Two and a half times the fastest rate the
// host samples at, so a reader never waits a turn for a value it just
// asked for — and no faster, because a turn nobody reads is a copy
// nobody wanted.
inline constexpr std::uint32_t kPeriodUs = 20'000;

// How much one turn may copy. Above today's whole set, so it bounds a
// set that grows rather than dividing the one that exists: adding a
// slot lengthens the sweep instead of lengthening an interrupt.
inline constexpr std::uint32_t kBudgetBytes = NOVA_TLM_PAYLOAD_BYTES;

// Bind the region, collect what every component offers, open it, and
// arm the turn. Primary core, RuntimeStart, after soft_timer has
// claimed its PPI.
void start() noexcept;

// The instant of the last turn taken, or zero before the first.
//
// A component whose published memory shadows registers that live in
// hardware refreshes that memory against this value: it names the
// cadence rather than approximating it with a period, and reading it
// costs a load where reading the counter costs an access. Stamped into
// the shadow it also dates it — one value, so the rate a shadow is
// taken at and the age it reports cannot disagree.
[[nodiscard]] auto last_turn() noexcept -> std::uint64_t;

} // namespace telemetry

struct telemetry_component {
  constexpr static auto INIT = flow::action<"telemetry_init">([]() noexcept { telemetry::start(); });

  constexpr static auto config = cib::config(cib::exports<TelemetryService>, cib::extend<cib::RuntimeStart>(*INIT));
};

} // namespace nova
