#pragma once

// components/trap_handler/include/trap_handler/guest_fault.hpp
//
// GuestFaultService — escalation point for unrecoverable guest faults
// (unclaimed MMIO, unemulatable aborts). The subscriber that owns VM
// lifecycles (core_vcpu) sets `handled` and retires the offending
// VCPU — other VMs keep running. With no subscriber the dispatcher
// halts.

#include "nova/arch/trap_context.hpp"

#include <nexus/callback.hpp>

namespace nova {

struct GuestFaultCall {
  TrapContext* ctx     = nullptr;
  bool         handled = false;
};

struct GuestFaultService : public callback::service<GuestFaultCall*> {};

} // namespace nova
