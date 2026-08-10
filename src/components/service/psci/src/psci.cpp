// components/psci/src/psci.cpp

#include "psci/psci.hpp"

#include "core_vcpu/core_vcpu.hpp"
#include "hal/console.hpp"
#include "hal/cpu.hpp"
#include "nova/abi/guest.hpp"
#include "nova/arch/trap_context.hpp"
#include "psci/psci_model.hpp"
#include "psci/smccc_model.hpp"
#include "smp/smp.hpp"
#include "trace/trace.hpp"
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
  if (call->handled) {
    return;
  }
  // The SMCCC Arch service shares this subscriber: same conduit, same
  // guest-facing firmware interface, and PSCI_FEATURES answers for both.
  if (const smccc::Verdict a = smccc::dispatch(call->func_id, call->ctx->x[1], cpu::speculation()); a.claimed) {
    call->handled   = true;
    call->ctx->x[0] = a.ret;
    return;
  }

  const psci::Verdict v = psci::dispatch(call->func_id, call->ctx->x[1]);
  if (!v.claimed) {
    return; // not ours — leave unclaimed for other subscribers
  }
  call->handled = true;
  // The power call itself, not the SMCCC arch service that shares this
  // subscriber: the edge is about a VM's power state changing.
  trace_emit(NOVA_TRACE_EV_PSCI, call->func_id, call->ctx->x[1], static_cast<std::uint64_t>(v.action));

  switch (v.action) {
  case psci::Action::kSystemOff:
    // Does not return to the caller — the whole VM retires here (the
    // caller's own vCPU last; foreign siblings via cross-call).
    log_power_event(" system_off\n");
    // SYSTEM_OFF has no return value: a guest that asked to be powered
    // off while a reset already owned the VM still ends up off.
    static_cast<void>(smp::stop_vm(vm_of(vcpu::current_index()), call->ctx));
    return;
  case psci::Action::kSystemReset:
    // Accepted resets do not return: the live frame is replaced by the
    // fresh boot context, or the caller is retired. A request collision
    // can still report INTERNAL_FAILURE to the guest.
    log_power_event(" system_reset\n");
    if (!smp::reset_vm(vm_of(vcpu::current_index()), call->ctx)) {
      call->ctx->x[0] = static_cast<std::uint64_t>(PSCI_INTERNAL_FAILURE);
    }
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
    } else {
      call->ctx->x[0] = static_cast<std::uint64_t>(smp::cpu_on(slot_of(vm, t), call->ctx->x[2], call->ctx->x[3]));
    }
    return;
  }
  case psci::Action::kCpuOff:
    // Does not return to the caller — only this vCPU retires; its
    // siblings keep running. The last vCPU promotes this to VM stop so
    // assigned DMA cannot survive without an owner.
    smp::cpu_off(vcpu::current_index(), call->ctx);
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
      switch (vcpu::power_state(slot_of(vm, t))) {
      case vcpu::PowerState::kOff:
        call->ctx->x[0] = PSCI_AFFINITY_OFF;
        break;
      case vcpu::PowerState::kOnPending:
        call->ctx->x[0] = PSCI_AFFINITY_ON_PENDING;
        break;
      case vcpu::PowerState::kOn:
        call->ctx->x[0] = PSCI_AFFINITY_ON;
        break;
      }
    }
    return;
  }
  case psci::Action::kNone:
    call->ctx->x[0] = v.ret;
    return;
  }
}

} // namespace nova
