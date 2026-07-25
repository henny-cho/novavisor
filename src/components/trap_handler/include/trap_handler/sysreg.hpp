#pragma once

// components/trap_handler/include/trap_handler/sysreg.hpp
//
// SysregService — one trapped MSR/MRS (EC 0x18), dispatched to every
// subscriber with the decoded (Op0, Op1, CRn, CRm, Op2, Rt) tuple.
// A subscriber emulates the effect (reading the operand from
// ctx->x[rt] for a write, storing the result there for a read; rt 31
// is xzr); the router advances ELR only after a subscriber claims the
// trap, so fault diagnostics keep the offending instruction.
//
// An unclaimed sysreg trap dumps state and escalates to
// GuestFaultService — the trap only exists because the hypervisor
// asked for it (e.g. ICH_HCR_EL2.TC), so a miss must surface early.

#include "nova/arch/sysreg_trap.hpp"
#include "nova/arch/trap_context.hpp"

#include <nexus/callback.hpp>

namespace nova {

struct SysregCall {
  TrapContext*    ctx = nullptr;
  esr::SysregTrap sysreg{};
  bool            handled = false;
};

struct SysregService : public callback::service<SysregCall*> {};

} // namespace nova
