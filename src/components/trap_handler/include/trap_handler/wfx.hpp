#pragma once

// components/trap_handler/include/trap_handler/wfx.hpp
//
// WfxService — one trapped guest WFI/WFE (EC 0x01, enabled by
// HCR_EL2.TWI|TWE). The router advances ELR past the instruction
// before dispatch, so a handler that parks the VCPU resumes it after
// the wfi once it is woken.
//
// If no subscriber claims the call the trap degrades to a NOP — an
// architecturally valid WFI/WFE implementation.

#include "nova/arch/trap_context.hpp"

#include <nexus/callback.hpp>

namespace nova {

struct WfxCall {
  TrapContext* ctx     = nullptr;
  bool         is_wfe  = false; // ESR ISS.TI: 0 = wfi, 1 = wfe
  bool         handled = false;
};

struct WfxService : public callback::service<WfxCall*> {};

} // namespace nova
