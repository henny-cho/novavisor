// components/smp/src/vm_lifecycle.cpp
//
// The VM lifecycle coordinator. Cold start, warm reset, stop, and vCPU
// power transitions all run on the VM boot vCPU's owner core and are
// serialized by that VM's atomic lifecycle token. Reset and stop share
// one quiesce protocol: every live vCPU is retired and DMA is drained
// and detached before memory is restored.

#include "hal/console.hpp"
#include "hal/cpu.hpp"
#include "hal/timer.hpp"
#include "nova/abi/guest.hpp"
#include "nova/abi/hvc_abi.h"
#include "nova/abi/psci.h"
#include "nova/arch/trap_context.hpp"
#include "smp/dma_quiesce.hpp"
#include "smp/quiesce_model.hpp"
#include "smp/smp.hpp"
#include "smp_internal.hpp"
#include "soft_timer/soft_timer.hpp"

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>

namespace nova::smp {

std::array<lifecycle::QuiesceTracker<kMaxVcpusPerVm>, kMaxGuests> g_lifecycle;
std::array<std::atomic<std::uint64_t>, kMaxGuests>                g_lifecycle_token{};

std::array<LifecycleMode, kMaxGuests> g_lifecycle_mode{};
std::array<bool, kMaxGuests>          g_dma_pending{};
std::array<bool, kMaxGuests>          g_dma_failed{};

namespace {

void on_lifecycle_timeout(TrapContext* ctx, std::uint64_t arg) noexcept;
void on_dma_drain(TrapContext* ctx, std::uint64_t arg) noexcept;

void release_lifecycle(std::size_t vm) noexcept {
  if (vm < g_lifecycle_token.size()) {
    g_lifecycle_token[vm].store(kLifecycleInactive, std::memory_order_release);
  }
  vcpu::end_lifecycle_transition();
}

void finish_lifecycle(std::size_t vm) noexcept {
  auto& tracker = g_lifecycle[vm];
  if (!tracker.ready() || g_dma_pending[vm]) {
    return;
  }

  soft_timer::cancel(soft_timer::kSlotLifecycle + vm);
  soft_timer::cancel(soft_timer::kSlotDmaDrain + vm);

  if (g_dma_failed[vm]) {
    (void)tracker.finish();
    g_lifecycle_mode[vm] = LifecycleMode::kNone;
    vcpu::end_lifecycle_transition();
    console::write("[smp] VM ");
    console::write_dec64(vm);
    console::write(" stopped after DMA isolation failure\n");
    return; // keep the lifecycle token latched
  }

  if (g_lifecycle_mode[vm] == LifecycleMode::kStop) {
    (void)tracker.finish();
    g_lifecycle_mode[vm] = LifecycleMode::kNone;
    release_lifecycle(vm);
    console::write("[smp] VM ");
    console::write_dec64(vm);
    console::write(" stopped\n");
    return;
  }

  console::write("[smp] VM ");
  console::write_dec64(vm);
  console::write(" quiesced — restoring\n");
  const std::uint64_t generation = vcpu::prepare_reset_quiesced_vm(vm);
  bool                restarted  = false;
  if (generation != 0U && smp::dma_resume_vm(vm, generation)) {
    restarted = vcpu::publish_reset_vm(vm, generation);
    if (!restarted) {
      static_cast<void>(smp::dma_begin_quiesce(vm));
    }
  }
  (void)tracker.finish();
  g_lifecycle_mode[vm] = LifecycleMode::kNone;
  release_lifecycle(vm);
  if (!restarted) {
    console::write("[smp] VM ");
    console::write_dec64(vm);
    console::write(" reset left stopped\n");
  }
}

void arm_lifecycle_timeout(std::size_t vm) noexcept {
  soft_timer::arm(soft_timer::kSlotLifecycle + vm, hyp_timer::deadline_after_ms(kQuiesceTimeoutMs),
                  &on_lifecycle_timeout, vm);
}

void arm_dma_poll(std::size_t vm) noexcept {
  soft_timer::arm(soft_timer::kSlotDmaDrain + vm, hyp_timer::deadline_after_ms(kDmaPollMs), &on_dma_drain, vm);
}

void on_dma_drain(TrapContext* /*ctx*/, std::uint64_t arg) noexcept {
  const auto vm = static_cast<std::size_t>(arg);
  if (vm >= guest_table().size() || !g_dma_pending[vm] || lifecycle_token(vm) == kLifecycleInactive) {
    return;
  }

  switch (smp::dma_poll_quiesce(vm)) {
  case DmaQuiesceResult::kPending:
    arm_dma_poll(vm);
    return;
  case DmaQuiesceResult::kComplete:
    g_dma_pending[vm] = false;
    break;
  case DmaQuiesceResult::kFailed:
    g_dma_pending[vm] = false;
    g_dma_failed[vm]  = true;
    break;
  }
  finish_lifecycle(vm);
}

void on_lifecycle_timeout(TrapContext* /*ctx*/, std::uint64_t arg) noexcept {
  const auto vm = static_cast<std::size_t>(arg);
  if (vm >= guest_table().size()) {
    return;
  }

  const std::uint64_t epoch   = lifecycle_token(vm);
  auto&               tracker = g_lifecycle[vm];
  switch (tracker.on_timeout(epoch)) {
  case lifecycle::TimeoutResult::kIgnored:
    return;
  case lifecycle::TimeoutResult::kRetry: {
    console::write("[smp] VM ");
    console::write_dec64(vm);
    console::write(" quiesce retry ");
    console::write_dec64(tracker.retries());
    console::write(" pending mask 0x");
    console::write_hex64(tracker.pending_mask());
    console::write("\n");

    const std::uint32_t pending = tracker.pending_mask();
    const std::size_t   vcpus   = guest_table()[vm].vcpus;
    for (std::size_t v = 0; v < vcpus; ++v) {
      if ((pending & (std::uint32_t{1} << v)) == 0U) {
        continue;
      }
      const std::size_t slot  = slot_of(vm, v);
      const std::size_t owner = slot_cpu(slot);
      if (owner == cpu::id()) {
        (void)vcpu::retire_vcpu(slot);
        acknowledge_quiesce(slot, epoch);
      } else {
        (void)enqueue(owner, {.op = Op::kQuiesceVcpu, .idx = static_cast<std::uint32_t>(slot), .a = epoch, .b = 0},
                      true);
      }
    }
    if (lifecycle_token(vm) == epoch && tracker.active()) {
      arm_lifecycle_timeout(vm);
    }
    return;
  }
  case lifecycle::TimeoutResult::kFailed:
    // Memory must remain untouched: at least one vCPU may still be
    // executing it. Keep the epoch token latched so new VM_START and
    // CPU_ON requests are denied, while late quiesce requests for this
    // epoch may still retire their targets. Other VMs keep running.
    console::write("[smp] VM ");
    console::write_dec64(vm);
    console::write(" lifecycle timed out — isolated, pending mask 0x");
    console::write_hex64(tracker.pending_mask());
    console::write("\n");
    const std::uint32_t pending = tracker.pending_mask();
    const std::size_t   vcpus   = guest_table()[vm].vcpus;
    for (std::size_t v = 0; v < vcpus; ++v) {
      if ((pending & (std::uint32_t{1} << v)) != 0U) {
        vcpu::cancel_start(slot_of(vm, v));
      }
    }
    tracker.cancel();
    vcpu::end_lifecycle_transition();
    return;
  }
}

void recover_dma_fault(std::size_t vm, std::uint64_t stream_id, std::uint64_t generation,
                       std::uint64_t /*unused*/) noexcept {
  if (vm >= guest_table().size() || vcpu::vm_generation(vm) != generation || !vcpu::vm_on(vm)) {
    return;
  }

  console::write("[smp] DMA fault in VM ");
  console::write_dec64(vm);
  console::write(" sid ");
  console::write_dec64(stream_id);
  console::write(" generation ");
  console::write_dec64(generation);
  console::write(" — resetting\n");
  if (!reset_vm(vm, nullptr)) {
    console::write("[smp] DMA fault recovery already covered by lifecycle\n");
  }
}

// Token-serialized stop, without the vm_on gate: cpu_off's last-out
// caller arrives with every vCPU already published off, and the empty
// live mask drives the same quiesce machinery (DMA detach, tracker
// bookkeeping) straight to completion.
//
// Reports acceptance for the same reason reset_vm does: both run the
// one quiesce protocol, so a caller that can be told "a lifecycle
// already owns this VM" for one must be told it for the other.
// Retiring the caller's own vCPU is separate from that verdict, which
// is why a refusal can still schedule away.
auto request_stop(std::size_t vm, TrapContext* live, bool self_retired) noexcept -> bool {
  std::uint64_t expected = kLifecycleInactive;
  if (!g_lifecycle_token[vm].compare_exchange_strong(expected, kLifecycleReserved, std::memory_order_acq_rel)) {
    if (self_retired) {
      vcpu::schedule_after_retire(live); // a concurrent lifecycle owns the VM; we still gave up our frame
    }
    return false;
  }
  vcpu::begin_lifecycle_transition();

  const std::size_t self = vcpu::current_index();
  const std::size_t boot = slot_of(vm);
  if (slot_cpu(boot) == cpu::id()) {
    const BeginLifecycleResult result = begin_lifecycle_local(vm, LifecycleMode::kStop);
    if (result.schedule_required || self_retired) {
      vcpu::schedule_after_retire(live);
    }
    return result.accepted;
  }

  if (!enqueue(slot_cpu(boot), {.op = Op::kBeginStop, .idx = static_cast<std::uint32_t>(vm), .a = 0, .b = 0}, true)) {
    release_lifecycle(vm);
    console::write("[smp] stop rejected: coordinator mailbox unavailable\n");
    if (self_retired) {
      vcpu::schedule_after_retire(live);
    }
    return false;
  }
  if (self_retired || (self < kMaxVcpus && vm_of(self) == vm && vcpu::retire_vcpu(self))) {
    vcpu::schedule_after_retire(live);
  }
  return true;
}

} // namespace

auto lifecycle_token(std::size_t vm) noexcept -> std::uint64_t {
  return g_lifecycle_token[vm].load(std::memory_order_acquire);
}

auto lifecycle_blocks_start(std::size_t vm) noexcept -> bool {
  return vm < g_lifecycle_token.size() && lifecycle_token(vm) != kLifecycleInactive;
}

auto start_vm_local(std::size_t vm) noexcept -> bool {
  const std::uint64_t generation = vcpu::prepare_start_vm(vm);
  if (generation == 0U) {
    return false;
  }
  if (!smp::dma_resume_vm(vm, generation)) {
    vcpu::cancel_start(slot_of(vm));
    return false;
  }
  if (vcpu::publish_start_vm(vm, generation)) {
    return true;
  }
  static_cast<void>(smp::dma_begin_quiesce(vm));
  return false;
}

void acknowledge_quiesce(std::size_t slot, std::uint64_t epoch) noexcept {
  const std::size_t vm = vm_of(slot);
  if (vm >= guest_table().size() || lifecycle_token(vm) != epoch) {
    return;
  }
  const lifecycle::AckResult result = g_lifecycle[vm].acknowledge(vcpu_of(slot), epoch);
  if (result == lifecycle::AckResult::kReady) {
    finish_lifecycle(vm);
  }
}

// Runs only on the VM boot VCPU's owner. A successful request can also
// require scheduling away from a live frame retired during quiesce.
auto begin_lifecycle_local(std::size_t vm, LifecycleMode mode) noexcept -> BeginLifecycleResult {
  if (vm >= guest_table().size() || lifecycle_token(vm) != kLifecycleReserved) {
    return {}; // stale begin request; its lifecycle was already resolved
  }
  if (slot_cpu(slot_of(vm)) != cpu::id()) {
    release_lifecycle(vm);
    return {};
  }

  const std::size_t vcpus     = guest_table()[vm].vcpus;
  std::uint32_t     live_mask = 0;
  for (std::size_t v = 0; v < vcpus; ++v) {
    if (vcpu::vcpu_on(slot_of(vm, v))) {
      live_mask |= std::uint32_t{1} << v;
    }
  }

  auto&      tracker = g_lifecycle[vm];
  const auto plan    = tracker.begin(live_mask);
  if (!plan.accepted) {
    tracker.cancel();
    release_lifecycle(vm);
    return {};
  }
  g_lifecycle_token[vm].store(plan.epoch, std::memory_order_release);
  g_lifecycle_mode[vm] = mode;
  g_dma_pending[vm]    = false;
  g_dma_failed[vm]     = false;
  console::write("[smp] VM ");
  console::write_dec64(vm);
  console::write(mode == LifecycleMode::kReset ? " reset epoch " : " stop epoch ");
  console::write_dec64(plan.epoch);
  console::write(" pending mask 0x");
  console::write_hex64(plan.pending_mask);
  console::write("\n");

  // Validate every foreign owner before sending anything. Once one
  // quiesce command is visible, cancellation may stop only part of a VM.
  for (std::size_t v = 0; v < vcpus; ++v) {
    if ((live_mask & (std::uint32_t{1} << v)) == 0U) {
      continue;
    }
    const std::size_t owner = slot_cpu(slot_of(vm, v));
    if (owner != cpu::id() && (owner >= g_online.size() || !g_online[owner].load(std::memory_order_acquire))) {
      tracker.cancel();
      release_lifecycle(vm);
      console::write("[smp] lifecycle rejected: target core offline\n");
      return {};
    }
  }

  switch (smp::dma_begin_quiesce(vm)) {
  case DmaQuiesceResult::kComplete:
    break;
  case DmaQuiesceResult::kPending:
    g_dma_pending[vm] = true;
    arm_dma_poll(vm);
    break;
  case DmaQuiesceResult::kFailed:
    g_dma_failed[vm] = true;
    break;
  }

  arm_lifecycle_timeout(vm);

  for (std::size_t v = 0; v < vcpus; ++v) {
    if ((live_mask & (std::uint32_t{1} << v)) == 0U) {
      continue;
    }
    const std::size_t slot  = slot_of(vm, v);
    const std::size_t owner = slot_cpu(slot);
    if (owner != cpu::id() &&
        !enqueue(owner, {.op = Op::kQuiesceVcpu, .idx = static_cast<std::uint32_t>(slot), .a = plan.epoch, .b = 0},
                 true)) {
      // Keep the epoch active: the timeout path retries this exact
      // pending bit. Cancelling after another core already quiesced
      // would leave the VM only partially alive.
      console::write("[smp] quiesce send deferred: mailbox full\n");
    }
  }

  bool schedule_required = false;
  for (std::size_t v = 0; v < vcpus; ++v) {
    if ((live_mask & (std::uint32_t{1} << v)) == 0U) {
      continue;
    }
    const std::size_t slot = slot_of(vm, v);
    if (slot_cpu(slot) == cpu::id()) {
      schedule_required = vcpu::retire_vcpu(slot) || schedule_required;
      acknowledge_quiesce(slot, plan.epoch);
    }
  }

  if (tracker.ready()) {
    finish_lifecycle(vm);
  }
  return {.accepted = true, .schedule_required = schedule_required};
}

auto start_vm(std::size_t vm) noexcept -> bool {
  if (vm >= guest_table().size() || lifecycle_blocks_start(vm) || vcpu::vm_on(vm) || !smp::dma_can_start(vm)) {
    return false;
  }
  const std::size_t boot = slot_of(vm);
  if (!vcpu::reserve_start(boot)) {
    return false;
  }
  if (lifecycle_blocks_start(vm)) {
    vcpu::cancel_start(boot);
    return false;
  }
  if (slot_cpu(boot) == cpu::id()) {
    return start_vm_local(vm);
  }
  if (!enqueue(slot_cpu(boot), {.op = Op::kStartVm, .idx = static_cast<std::uint32_t>(vm), .a = 0, .b = 0})) {
    vcpu::cancel_start(boot);
    return false;
  }
  return true;
}

auto cpu_on(std::size_t slot, std::uint64_t entry, std::uint64_t context_id) noexcept -> std::int32_t {
  if (!valid_slot(slot)) {
    return PSCI_INVALID_PARAMETERS;
  }
  const std::size_t vm = vm_of(slot);
  if (lifecycle_blocks_start(vm)) {
    return PSCI_DENIED;
  }
  if (!vcpu::reserve_start(slot)) {
    switch (vcpu::power_state(slot)) {
    case vcpu::PowerState::kOn:
      return PSCI_ALREADY_ON;
    case vcpu::PowerState::kOnPending:
      return PSCI_ON_PENDING;
    case vcpu::PowerState::kOff:
      return PSCI_INTERNAL_FAILURE;
    }
  }
  if (lifecycle_blocks_start(vm)) {
    vcpu::cancel_start(slot);
    return PSCI_DENIED;
  }
  if (slot_cpu(slot) == cpu::id()) {
    return vcpu::start_vcpu(slot, entry, context_id) ? PSCI_SUCCESS : PSCI_INVALID_PARAMETERS;
  }
  // A queued start is "accepted": the caller observes the boot through
  // its own synchronization, per SMP firmware convention.
  if (!enqueue(slot_cpu(slot),
               {.op = Op::kCpuOn, .idx = static_cast<std::uint32_t>(slot), .a = entry, .b = context_id})) {
    vcpu::cancel_start(slot);
    return PSCI_INTERNAL_FAILURE;
  }
  return PSCI_SUCCESS;
}

auto stop_vm(std::size_t vm, TrapContext* live) noexcept -> bool {
  if (vm >= guest_table().size() || !vcpu::vm_on(vm)) {
    return false;
  }
  return request_stop(vm, live, /*self_retired=*/false);
}

void cpu_off(std::size_t slot, TrapContext* live) noexcept {
  if (!valid_slot(slot)) {
    return;
  }
  // Retire first, then decide: the published Off transition is atomic,
  // so among concurrent sibling CPU_OFFs whoever then observes the VM
  // fully off funnels into the stop path, whose lifecycle-token CAS
  // makes a duplicate caller a no-op. No lock is held across the
  // retirement's timer reprogramming (the old per-VM power lock was
  // this file's only second serialization mechanism).
  const std::size_t vm      = vm_of(slot);
  const bool        retired = vcpu::retire_vcpu(slot);
  if (!vcpu::vm_on(vm)) {
    // A concurrent sibling may own the stop already; either way this
    // caller's vCPU is off, which is all CPU_OFF promises.
    static_cast<void>(request_stop(vm, live, retired));
    return;
  }
  if (retired) {
    vcpu::schedule_after_retire(live);
  }
}

auto reset_vm(std::size_t vm, TrapContext* live) noexcept -> bool {
  if (vm >= guest_table().size() || !vcpu::vm_on(vm)) {
    return false;
  }

  std::uint64_t expected = kLifecycleInactive;
  if (!g_lifecycle_token[vm].compare_exchange_strong(expected, kLifecycleReserved, std::memory_order_acq_rel)) {
    return false; // a reset for this VM is already in flight
  }
  vcpu::begin_lifecycle_transition();

  const std::size_t boot = slot_of(vm);
  if (slot_cpu(boot) == cpu::id()) {
    const BeginLifecycleResult result = begin_lifecycle_local(vm, LifecycleMode::kReset);
    if (result.schedule_required) {
      vcpu::schedule_after_retire(live);
    }
    return result.accepted;
  }

  if (!enqueue(slot_cpu(boot), {.op = Op::kBeginReset, .idx = static_cast<std::uint32_t>(vm), .a = 0, .b = 0}, true)) {
    release_lifecycle(vm);
    console::write("[smp] reset rejected: coordinator mailbox unavailable\n");
    return false;
  }

  // SYSTEM_RESET must not return to a secondary caller while the boot
  // owner coordinates the VM. Publish it off after the begin request is
  // visible; the coordinator's live mask will then exclude it.
  const std::size_t self = vcpu::current_index();
  if (self < kMaxVcpus && vm_of(self) == vm && vcpu::retire_vcpu(self)) {
    vcpu::schedule_after_retire(live);
  }
  return true;
}

} // namespace nova::smp

namespace nova {

namespace {

// The three power operations, offered to the host. Each is the function
// its guest-facing twin already calls, so nothing new happens in EL2 —
// only a second caller reaches it. Out of range is told apart from
// refused: the row advertises the VM band, and an advertised band the
// handler answered STATE for would be a band it did not really enforce.
auto command_stop(const command::Record& c, TrapContext* ctx) noexcept -> std::uint64_t {
  if (c.a >= guest_table().size()) {
    return NOVA_CMD_RESULT_RANGE;
  }
  return smp::stop_vm(static_cast<std::size_t>(c.a), ctx) ? NOVA_CMD_RESULT_OK : NOVA_CMD_RESULT_STATE;
}

auto command_reset(const command::Record& c, TrapContext* ctx) noexcept -> std::uint64_t {
  if (c.a >= guest_table().size()) {
    return NOVA_CMD_RESULT_RANGE;
  }
  return smp::reset_vm(static_cast<std::size_t>(c.a), ctx) ? NOVA_CMD_RESULT_OK : NOVA_CMD_RESULT_STATE;
}

auto command_start(const command::Record& c, TrapContext* /*ctx*/) noexcept -> std::uint64_t {
  if (c.a >= guest_table().size()) {
    return NOVA_CMD_RESULT_RANGE;
  }
  // No frame: starting a VM does not retire the caller.
  return smp::start_vm(static_cast<std::size_t>(c.a)) ? NOVA_CMD_RESULT_OK : NOVA_CMD_RESULT_STATE;
}

} // namespace

// The band is the same expression the handlers check against, one line
// apart, so what the page offers and what a command is refused for
// cannot drift.
void smp_component::commands(CommandCall* call) noexcept {
  const auto         last = static_cast<std::uint32_t>(guest_table().size() - 1);
  const command::Arg vm{.kind = NOVA_CMD_ARG_VM, .lo = 0, .hi = last};
  call->declare({.op = NOVA_CMD_OP_STOP, .words = 1, .a = vm, .run = &command_stop});
  call->declare({.op = NOVA_CMD_OP_RESET, .words = 1, .a = vm, .run = &command_reset});
  call->declare({.op = NOVA_CMD_OP_START, .words = 1, .a = vm, .run = &command_start});
}

void smp_component::handle_hvc(HvcCall* call) noexcept {
  if (call->func_id != NOVA_HVC_FN_VM_START) {
    return; // not ours — leave unclaimed for other subscribers
  }
  call->handled   = true;
  call->ctx->x[0] = smp::start_vm(static_cast<std::size_t>(call->ctx->x[1])) ? 0 : kSmcccNotSupported;
}

void smp_component::handle_guest_fault(GuestFaultCall* call) noexcept {
  call->handled          = true;
  const std::size_t slot = vcpu::current_index();
  const std::size_t vm   = vm_of(slot);
  console::write("[smp] guest fault in VM ");
  console::write_dec64(vm);
  console::write(" vCPU ");
  console::write_dec64(vcpu_of(slot));
  console::write(" — resetting\n");
  if (!smp::reset_vm(vm, call->ctx)) {
    // Both refused means a lifecycle already owns the VM, which is the
    // recovery this fault wanted.
    console::write("[smp] guest fault recovery unavailable — stopping VM\n");
    static_cast<void>(smp::stop_vm(vm, call->ctx));
  }
}

void smp_component::handle_dma_fault(DmaFaultCall* call) noexcept {
  call->handled                  = true;
  const dma::FaultNotice& notice = call->notice;
  if (!notice.valid() || notice.owner_vm >= guest_table().size() || !smp::g_online[0].load(std::memory_order_acquire)) {
    return;
  }

  const std::size_t owner = smp::slot_cpu(slot_of(notice.owner_vm));
  if (owner == cpu::id()) {
    smp::recover_dma_fault(notice.owner_vm, notice.stream_id, notice.generation, 0);
    return;
  }
  if (!smp::enqueue(owner,
                    {.op       = smp::Op::kVmOwnerCall,
                     .idx      = static_cast<std::uint32_t>(notice.owner_vm),
                     .a        = notice.stream_id,
                     .b        = notice.generation,
                     .c        = 0,
                     .callback = &smp::recover_dma_fault},
                    true)) {
    console::write("[smp] DMA fault owner routing failed; stream remains quarantined\n");
  }
}

} // namespace nova
