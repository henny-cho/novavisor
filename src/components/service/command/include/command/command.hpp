#pragma once

// Command Component
//
// The host's one way into a running machine: places the command ring's
// page and drains it on a period of its own.
//
// Why a period of its own. The scheduler's slice is cancelled whenever
// nothing is competing for a core, so a single-guest machine and an
// idle core have no slice tick at all — a command riding it would wait
// for something unrelated to happen, or forever. A dedicated slot is
// always armed, which turns the wait into a number this component
// declares and publishes in the page, rather than a property of how
// busy the machine happens to be.
//
// The cost is one callback per period on the primary: an acquire load,
// a comparison, and a re-arm when the ring is empty.
//
// Everything the host can ask for runs through execute(), including the
// opcodes whose effect is a single store. One entry point is one place
// that validates, and it is what keeps the page the only way in.

#include "nova/command.hpp"

#include <cib/top.hpp>
#include <cstdint>
#include <flow/flow.hpp>

namespace nova::command {

// How long a command may wait. The machine's existing coarse tick —
// the scheduler's slice is the same span — so this adds a cadence the
// firmware already runs at rather than a finer one, and it is short
// enough that a reader never has to wonder whether a command was lost.
inline constexpr std::uint32_t kPeriodUs = 10'000;

// Place the page and arm the drain. Primary core, RuntimeStart, after
// soft_timer has claimed its PPI.
void start() noexcept;

// Carry out one command and record what became of it. External on
// purpose: this is the moment the event catalogue names, so a reader
// can break here and hold the machine at the instant a command takes
// effect.
void execute(const Record& command) noexcept;

} // namespace nova::command

namespace nova {

struct command_component {
  constexpr static auto INIT = flow::action<"command_init">([]() noexcept { command::start(); });

  constexpr static auto config = cib::config(cib::extend<cib::RuntimeStart>(*INIT));
};

} // namespace nova
