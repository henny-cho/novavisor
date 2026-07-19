// components/psci/src/psci.cpp

#include "psci/psci.hpp"

#include "core_vcpu/core_vcpu.hpp"
#include "hal/console.hpp"
#include "nova/abi/guest.hpp"
#include "nova/arch/trap_context.hpp"
#include "psci/psci_model.hpp"
#include "smp/smp.hpp"
#include "vgic/vgic.hpp"

namespace nova {
namespace {

void log_power_event(const char* what) noexcept {
  console::write("[psci] VM ");
  console::write_dec64(vm_of(vcpu::current_index()));
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
    // Does not return to the caller — the whole VM retires here (the
    // caller's own vCPU last; foreign siblings via cross-call).
    log_power_event(" system_off\n");
    smp::stop_vm(vm_of(vcpu::current_index()), call->ctx);
    return;
  case psci::Action::kSystemReset:
    // Does not return either way: on success the live frame now holds
    // the freshly seeded boot context (when vcpu 0 called; a secondary
    // caller is stopped by the fan-out); on an exhausted restart
    // budget the VM was stopped and the scheduler moved on.
    log_power_event(" system_reset\n");
    smp::reset_vm(vm_of(vcpu::current_index()), call->ctx);
    return;
  case psci::Action::kCpuOn: {
    // The target names a sibling vCPU of the calling VM; the start is
    // affinity-routed (a remote SUCCESS means "accepted" — the caller
    // observes the boot through its own synchronization, per SMP
    // firmware convention).
    const std::size_t   vm = vm_of(vcpu::current_index());
    const std::uint64_t t  = psci::target_vcpu(call->ctx->x[1]);
    if (t == psci::kInvalidTarget || t >= guest_table()[vm].vcpus) {
      call->ctx->x[0] = static_cast<std::uint64_t>(PSCI_INVALID_PARAMETERS);
    } else if (vcpu::vcpu_on(slot_of(vm, t))) {
      call->ctx->x[0] = static_cast<std::uint64_t>(PSCI_ALREADY_ON);
    } else {
      call->ctx->x[0] = smp::cpu_on(slot_of(vm, t), call->ctx->x[2], call->ctx->x[3])
                            ? PSCI_SUCCESS
                            : static_cast<std::uint64_t>(PSCI_INVALID_PARAMETERS);
    }
    return;
  }
  case psci::Action::kCpuOff:
    // Does not return to the caller — only this vCPU retires; its
    // siblings keep running.
    vcpu::stop_vcpu(vcpu::current_index(), call->ctx);
    return;
  case psci::Action::kCpuSuspend:
    // Standby: park the caller exactly like a trapped WFI. A pending
    // wake-up event is architecturally an immediate return; otherwise
    // block with the timer-mirrored wake. x0 must read PSCI_SUCCESS on
    // wake, so seed it before the frame swap parks the caller.
    call->ctx->x[0] = v.ret;
    if (!vgic::has_deliverable(vcpu::current_index())) {
      vcpu::block_current(call->ctx);
    }
    return;
  case psci::Action::kAffinityInfo: {
    const std::size_t   vm = vm_of(vcpu::current_index());
    const std::uint64_t t  = psci::target_vcpu(call->ctx->x[1]);
    if (t == psci::kInvalidTarget || t >= guest_table()[vm].vcpus) {
      call->ctx->x[0] = static_cast<std::uint64_t>(PSCI_INVALID_PARAMETERS);
    } else {
      call->ctx->x[0] = vcpu::vcpu_on(slot_of(vm, t)) ? PSCI_AFFINITY_ON : PSCI_AFFINITY_OFF;
    }
    return;
  }
  case psci::Action::kNone:
    call->ctx->x[0] = v.ret;
    return;
  }
}

} // namespace nova
