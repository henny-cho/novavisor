#pragma once

// components/trap_handler/include/trap_handler/fp_simd.hpp
//
// FpSimdService — one trapped guest FP/SIMD access (EC 0x07, enabled
// by CPTR_EL2.TFP). Unlike WFx, ELR is NOT advanced: the handler
// switches the FP bank in and clears the trap, and the guest
// re-executes the access — this time successfully.
//
// An unclaimed call would re-trap forever, so the router escalates it
// to GuestFaultService instead.

#include "nova/arch/trap_context.hpp"

#include <nexus/callback.hpp>

namespace nova {

struct FpSimdCall {
  TrapContext* ctx     = nullptr;
  bool         handled = false;
};

struct FpSimdService : public callback::service<FpSimdCall*> {};

} // namespace nova
