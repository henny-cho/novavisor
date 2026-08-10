// components/smp/src/cross_call.cpp
//
// The cross-core mailbox and the guest SGI fan-out. A VCPU's state is
// touched only on its affinity core, so operations naming a foreign
// VM — HVC_VM_START, IVC doorbells — are enqueued into the owning
// core's mailbox and announced with a physical SGI; the receiver
// executes them locally in its IRQ drain.

#include "hal/console.hpp"
#include "hal/cpu.hpp"
#include "hal/gic.hpp"
#include "nova/abi/guest.hpp"
#include "nova/arch/esr.hpp"
#include "nova/arch/trap_context.hpp"
#include "nova/sync.hpp"
#include "smp/smp.hpp"
#include "smp_internal.hpp"
#include "trace/trace.hpp"
#include "vgic/vgic_model.hpp"

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>

namespace nova::smp {

std::array<Mailbox, cpu::kMaxCpus> g_mail;

static_assert(kMaxVcpus <= 32);
std::array<std::atomic<std::uint32_t>, cpu::kMaxCpus> g_reevaluate{};

std::array<std::atomic<bool>, cpu::kMaxCpus> g_online{};

auto enqueue(std::size_t target_cpu, Request r, bool lifecycle) noexcept -> bool {
  if (target_cpu >= g_online.size() || !g_online[target_cpu].load(std::memory_order_acquire)) {
    return false;
  }
  Mailbox& box = g_mail[target_cpu];
  {
    sync::Guard guard{box.lock};
    // Reserve room for one quiesce command and ACK per VM. A reset must
    // never deadlock because ordinary notifications filled the box.
    const std::size_t limit = lifecycle ? box.req.size() : box.req.size() - kLifecycleReserve;
    if (box.count >= limit) {
      return false; // burst beyond capacity — caller sees a rejected call
    }
    box.req[box.count++] = r;
  }
  gic::send_sgi(target_cpu, kCrossCallSgi);
  return true;
}

auto post_virq(std::size_t slot, std::uint32_t vintid) noexcept -> bool {
  if (!valid_slot(slot)) {
    return false;
  }
  if (slot_cpu(slot) == cpu::id()) {
    return vcpu::post_virq(slot, vintid);
  }
  return enqueue(slot_cpu(slot), {.op = Op::kPostVirq, .idx = static_cast<std::uint32_t>(slot), .a = vintid, .b = 0});
}

auto invoke_vm_owner(std::size_t vm, VmOwnerCall fn, std::uint64_t a, std::uint64_t b, std::uint64_t c) noexcept
    -> bool {
  if (vm >= guest_table().size() || fn == nullptr) {
    return false;
  }
  const std::size_t owner = slot_cpu(slot_of(vm));
  if (owner == cpu::id()) {
    fn(vm, a, b, c); // no wire crossed; nothing to record
    return true;
  }
  // Emitted only on the crossing branch: the edge this lights is drawn
  // between two cores, and a same-core call is not evidence for it.
  trace_emit(NOVA_TRACE_EV_CROSS_CALL, static_cast<std::uint32_t>(vm), owner);
  return enqueue(
      owner, {.op = Op::kVmOwnerCall, .idx = static_cast<std::uint32_t>(vm), .a = a, .b = b, .c = c, .callback = fn});
}

void reevaluate_virq(std::size_t slot) noexcept {
  if (!valid_slot(slot) || !vcpu::vcpu_on(slot)) {
    return;
  }
  const std::size_t owner = slot_cpu(slot);
  if (owner == cpu::id()) {
    vcpu::reevaluate_virq(slot);
    return;
  }
  if (owner >= g_online.size() || !g_online[owner].load(std::memory_order_acquire)) {
    return;
  }
  const std::uint32_t bit = std::uint32_t{1} << slot;
  if (g_reevaluate[owner].fetch_or(bit, std::memory_order_acq_rel) == 0U) {
    gic::send_sgi(owner, kCrossCallSgi);
  }
}

} // namespace nova::smp

namespace nova {

// Which cores are up, what work is queued for them, and where each VM
// is in a power transition.
void smp_component::telemetry(TelemetryCall* call) noexcept {
  call->declare(&smp::g_online, sizeof smp::g_online);
  call->declare(&smp::g_mail, sizeof smp::g_mail);
  call->declare(&smp::g_lifecycle, sizeof smp::g_lifecycle);
  call->declare(&smp::g_lifecycle_mode, sizeof smp::g_lifecycle_mode);
}

void smp_component::handle_virq_reevaluate(VirqReevaluateCall* call) noexcept {
  smp::reevaluate_virq(call->slot);
}

void smp_component::handle_sysreg(SysregCall* call) noexcept {
  if (!call->sysreg.write || !esr::is_icc_sgi1r(call->sysreg)) {
    return; // not ours (reads of trapped common regs stay unclaimed)
  }
  call->handled = true;

  const std::uint64_t value = call->sysreg.rt == esr::kSrtZeroReg ? 0 : call->ctx->x[call->sysreg.rt];
  const std::size_t   self  = vcpu::current_index();
  const std::size_t   vm    = vm_of(self);
  const std::uint32_t intid = vgic::sgi1r_intid(value);

  std::uint32_t targets = vgic::sgi1r_targets(value, vcpu_of(self), guest_table()[vm].vcpus);
  for (std::size_t t = 0; targets != 0U; ++t, targets >>= 1U) {
    if ((targets & 1U) != 0U) {
      (void)smp::post_virq(slot_of(vm, t), intid); // off targets drop the SGI — matches hardware
    }
  }
}

void smp_component::handle_irq(IrqCall* call) noexcept {
  if (call->handled) {
    return;
  }
  if (call->intid != smp::kCrossCallSgi) {
    return;
  }
  call->handled = true;

  // Copy the batch out first — executing under the lock would deadlock
  // against a sender targeting this core from another IRQ path. Only
  // the occupied entries move (typically one), not the whole box.
  smp::Mailbox&                                   box = smp::g_mail[cpu::id()];
  std::array<smp::Request, smp::kMailboxCapacity> batch;
  std::size_t                                     n = 0;
  {
    sync::Guard guard{box.lock};
    n         = box.count;
    box.count = 0;
    for (std::size_t i = 0; i < n; ++i) {
      batch[i] = box.req[i];
    }
  }
  bool schedule_required = false;
  for (std::size_t i = 0; i < n; ++i) {
    const smp::Request& r = batch[i];
    switch (r.op) {
    case smp::Op::kStartVm:
      if (smp::lifecycle_blocks_start(r.idx) || !smp::start_vm_local(r.idx)) {
        vcpu::cancel_start(slot_of(r.idx));
      }
      break;
    case smp::Op::kPostVirq:
      (void)vcpu::post_virq(r.idx, static_cast<std::uint32_t>(r.a));
      break;
    case smp::Op::kCpuOn:
      if (smp::lifecycle_blocks_start(vm_of(r.idx)) || !vcpu::start_vcpu(r.idx, r.a, r.b)) {
        vcpu::cancel_start(r.idx);
      }
      break;
    case smp::Op::kVmOwnerCall:
      if (r.callback != nullptr) {
        r.callback(r.idx, r.a, r.b, r.c);
      }
      break;
    case smp::Op::kBeginReset:
      schedule_required =
          smp::begin_lifecycle_local(r.idx, smp::LifecycleMode::kReset).schedule_required || schedule_required;
      break;
    case smp::Op::kBeginStop:
      schedule_required =
          smp::begin_lifecycle_local(r.idx, smp::LifecycleMode::kStop).schedule_required || schedule_required;
      break;
    case smp::Op::kQuiesceVcpu: {
      const std::size_t vm = vm_of(r.idx);
      if (smp::lifecycle_token(vm) != r.a) {
        break; // stale command from a completed or superseded reset
      }
      schedule_required       = vcpu::retire_vcpu(r.idx) || schedule_required;
      const std::size_t owner = smp::slot_cpu(slot_of(vm));
      if (!smp::enqueue(owner, {.op = smp::Op::kQuiesceAck, .idx = r.idx, .a = r.a, .b = 0}, true)) {
        console::write("[smp] failed to return quiesce ACK\n");
      }
      break;
    }
    case smp::Op::kQuiesceAck:
      smp::acknowledge_quiesce(r.idx, r.a);
      break;
    }
  }

  // Drain until stable: a writer racing exchange(0) either joins this
  // loop or observes zero and sends another SGI.
  for (;;) {
    std::uint32_t dirty = smp::g_reevaluate[cpu::id()].exchange(0, std::memory_order_acq_rel);
    if (dirty == 0U) {
      break;
    }
    for (std::size_t slot = 0; dirty != 0U; ++slot, dirty >>= 1U) {
      if ((dirty & 1U) != 0U) {
        vcpu::reevaluate_virq(slot);
      }
    }
  }
  if (schedule_required) {
    vcpu::schedule_after_retire(call->ctx);
  }
}

} // namespace nova
