#pragma once

// components/trap_handler/include/trap_handler/command.hpp
//
// Host command registration. Every component that carries out an opcode
// declares it here once, at init; the command ring walks what it
// collected to dispatch, and publishes the same table so a host offers
// exactly what this build accepts.
//
// The declaration lives with trap_handler's other service types for the
// same reason they do: a subscriber must compile without the publisher
// in the composition. VM power and the scheduler exist in profiles that
// place no command page, and cib rejects a subscriber whose service
// nobody exports — so the export sits in the component every profile
// composes rather than in the ring's own.
//
// Refusals are counted rather than returned, like TelemetryCall: a
// component offering three ops should not have to decide what to do
// when the third does not fit, and how many did not fit is one fact the
// machine reports once, at init.

#include "nova/command.hpp"

#include <cstdint>
#include <nexus/callback.hpp>

namespace nova {

struct CommandCall {
  command::OpTable* table   = nullptr;
  std::uint32_t     refused = 0;

  void declare(const command::Op& entry) noexcept {
    if (table == nullptr || !table->declare(entry)) {
      ++refused;
    }
  }
};

struct CommandService : public callback::service<CommandCall*> {};

} // namespace nova
