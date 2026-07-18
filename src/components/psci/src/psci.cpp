// components/psci/src/psci.cpp

#include "psci/psci.hpp"

#include "core_vcpu/core_vcpu.hpp"
#include "hal/console.hpp"
#include "nova/arch/trap_context.hpp"
#include "psci/psci_model.hpp"

namespace nova {
namespace {

void log_power_event(const char* what) noexcept {
  console::write("[psci] VM ");
  console::write_dec64(vcpu::current_index());
  console::write(what);
}

} // namespace

void psci_component::handle_hvc(HvcCall* call) noexcept {
  const psci::Verdict v = psci::dispatch(call->func_id, call->ctx->x[1]);
  if (!v.claimed) {
    return; // not ours — leave unclaimed for other subscribers
  }
  call->handled = true;

  switch (v.action) {
  case psci::Action::kSystemOff:
    // Does not return to the caller — the VCPU retires here.
    log_power_event(" system_off\n");
    vcpu::exit_current(call->ctx);
    return;
  case psci::Action::kSystemReset:
    // Does not return either way: on success the live frame now holds
    // the freshly seeded boot context; on an exhausted restart budget
    // the VM was stopped and the scheduler moved on.
    log_power_event(" system_reset\n");
    vcpu::reset_vm(vcpu::current_index(), call->ctx);
    return;
  case psci::Action::kNone:
    call->ctx->x[0] = v.ret;
    return;
  }
}

} // namespace nova
