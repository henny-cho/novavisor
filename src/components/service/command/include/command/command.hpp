#pragma once

// Command Component
//
// The host's one way into a running machine: places the command ring's
// page and drains it on a period of its own.
//
// Why a period of its own. The scheduler's slice is cancelled whenever
// nothing competes for a core, so a single-guest machine and an idle
// core have no slice tick at all and a command riding it would wait for
// something unrelated, or forever. A dedicated slot is always armed,
// which makes the wait a number this component declares and publishes
// in the page rather than a property of how busy the machine is.
//
// Everything the host can ask for runs through execute(), including the
// opcodes whose effect is a single store: one entry point is one place
// that validates, and it keeps the page the only way in.

#include "nova/command.hpp"

#include <cib/top.hpp>
#include <cstdint>
#include <flow/flow.hpp>

namespace nova::command {

// How long a command may wait, and the whole of the cost this component
// adds: one timer callback per period on the primary — an acquire load,
// a comparison, and a re-arm. A hundred a second is far below anything
// a reader notices and far above anything the machine does.
inline constexpr std::uint32_t kPeriodUs = 10'000;

// Place the page and arm the drain. Primary core, RuntimeStart, after
// soft_timer has claimed its PPI.
void start() noexcept;

// Carry out one command and record what became of it. External so the
// symbol survives: the event catalogue offers it as a stop point, and a
// reader breaking here holds the machine at the instant one takes
// effect.
void execute(const Record& command) noexcept;

} // namespace nova::command

namespace nova {

struct command_component {
  constexpr static auto INIT = flow::action<"command_init">([]() noexcept { command::start(); });

  constexpr static auto config = cib::config(cib::extend<cib::RuntimeStart>(*INIT));
};

} // namespace nova
