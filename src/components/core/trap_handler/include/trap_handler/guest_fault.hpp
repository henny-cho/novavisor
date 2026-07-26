#pragma once

// components/trap_handler/include/trap_handler/guest_fault.hpp
//
// GuestFaultService — escalation point for unrecoverable guest faults
// (unclaimed MMIO, unemulatable aborts). The VM lifecycle owner sets
// `handled` and starts bounded VM-wide recovery while other VMs keep
// running. With no subscriber the dispatcher halts.

#include "nova/arch/trap_context.hpp"

#include <nexus/callback.hpp>

namespace nova {

struct GuestFaultCall {
  TrapContext* ctx     = nullptr;
  bool         handled = false;
};

struct GuestFaultService : public callback::service<GuestFaultCall*> {};

} // namespace nova
