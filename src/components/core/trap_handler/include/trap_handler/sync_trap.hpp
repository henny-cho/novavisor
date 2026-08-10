#pragma once

// components/trap_handler/include/trap_handler/sync_trap.hpp
//
// EL2SyncTrapService — every synchronous exception taken from a lower
// EL, before any class routing. Subscribers see the frame as it
// arrived and every one of them runs: there is no claim, because the
// service names the moment rather than the work.
//
// Registration order between subscribers is unspecified, so a handler
// that needs the frame untouched by the router's ELR policy cannot
// rely on running first. What it can rely on is the frame being the
// guest's, and the syndrome fields being the ones the exception
// delivered — the ELR policy is the only thing the router moves.

#include "nova/arch/trap_context.hpp"

#include <nexus/callback.hpp>

namespace nova {

struct EL2SyncTrapService : public callback::service<TrapContext*> {};

} // namespace nova
