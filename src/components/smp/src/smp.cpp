// components/smp/src/smp.cpp
//
// Secondary bring-up sequence and the cross-core mailbox. The primary
// has finished every shared-state init (BSS, Stage 2 tables, GICD)
// before CPU_ON is issued, so a secondary only initializes what is
// banked per core, then enters its own scheduler.

#include "smp/smp.hpp"

#include "hal/console.hpp"
#include "hal/cpu.hpp"
#include "hal/gic.hpp"
#include "hal/timer.hpp"
#include "nova/abi/guest.hpp"
#include "nova/abi/hvc_abi.h"
#include "nova/abi/psci.h"
#include "nova/arch/data_abort.hpp" // esr::kSrtZeroReg
#include "nova/arch/trap_context.hpp"
#include "nova/sync.hpp"
#include "vgic/vgic_model.hpp"

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>

// boot.S label — PSCI CPU_ON entry point (EL2 runs flat, PC == PA).
extern "C" void nova_secondary_entry() noexcept;

namespace nova::smp {
namespace {

// Physical SGI announcing "your mailbox has work" — EL2's own IPI.
// Guests never see physical SGIs (they get vINTIDs via ICH_LR).
inline constexpr std::uint32_t kCrossCallSgi     = 0;
inline constexpr std::size_t   kMailboxCapacity  = 16;
inline constexpr std::size_t   kLifecycleReserve = 2 * kMaxGuests * (kMaxVcpusPerVm - 1);
static_assert(kLifecycleReserve < kMailboxCapacity);

enum class Op : std::uint8_t {
  kStartVm,
  kPostVirq,
  kCpuOn,
  kStopVcpu,
  kBeginReset,
  kQuiesceVcpu,
  kQuiesceAck,
};

// `idx` is a VM for start/begin-reset, a vCPU slot otherwise. a/b are
// wide enough for CPU_ON arguments and the reset epoch.
struct Request {
  Op            op  = Op::kStartVm;
  std::uint32_t idx = 0;
  std::uint64_t a   = 0;
  std::uint64_t b   = 0;
};

// One mailbox per core, written by any core under its lock, drained by
// the owner in IRQ context. Capacity covers the realistic burst (a
// couple of VMs' worth of doorbells); a full box rejects the request.
struct Mailbox {
  sync::SpinLock                        lock;
  std::array<Request, kMailboxCapacity> req{};
  std::size_t                           count = 0;
};

std::array<Mailbox, cpu::kMaxCpus> g_mail;

// Reset state is modified only on each VM boot VCPU's owner core. The
// atomic flag reserves a reset before its begin request crosses cores
// and prevents a concurrent cold start from reviving a quiescing VM.
std::array<lifecycle::QuiesceTracker<kMaxVcpusPerVm>, kMaxGuests> g_reset;
std::array<std::atomic<bool>, kMaxGuests>                         g_reset_active{};

// Set by each secondary as its last bring-up step; the primary's
// bounded wait reads it. acquire/release pairs the secondary's init
// writes with the primary's continuation.
std::array<std::atomic<bool>, cpu::kMaxCpus> g_online{};

// Bounded wait for one core to report online.
inline constexpr std::uint64_t kOnlineWaitMs = 100;

// A vCPU slot's owning core (per-vCPU affinity — not the VM's).
[[nodiscard]] auto slot_cpu(std::size_t slot) noexcept -> std::size_t {
  return guest_table()[vm_of(slot)].cpu[vcpu_of(slot)];
}

[[nodiscard]] auto valid_slot(std::size_t slot) noexcept -> bool {
  return vm_of(slot) < guest_table().size() && vcpu_of(slot) < guest_table()[vm_of(slot)].vcpus;
}

[[nodiscard]] auto enqueue(std::size_t target_cpu, Request r, bool lifecycle = false) noexcept -> bool {
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

void release_reset(std::size_t vm) noexcept {
  if (vm < g_reset_active.size()) {
    g_reset_active[vm].store(false, std::memory_order_release);
  }
  vcpu::end_lifecycle_transition();
}

void finish_reset(std::size_t vm) noexcept {
  auto& tracker = g_reset[vm];
  if (!tracker.ready()) {
    return;
  }

  console::write("[smp] VM ");
  console::write_dec64(vm);
  console::write(" quiesced — restoring\n");
  const bool restarted = vcpu::reset_quiesced_vm(vm);
  (void)tracker.finish();
  release_reset(vm);
  if (!restarted) {
    console::write("[smp] VM ");
    console::write_dec64(vm);
    console::write(" reset left stopped\n");
  }
}

void acknowledge_quiesce(std::size_t slot, std::uint64_t epoch) noexcept {
  const std::size_t vm = vm_of(slot);
  if (vm >= guest_table().size()) {
    return;
  }
  const lifecycle::AckResult result = g_reset[vm].acknowledge(vcpu_of(slot), epoch);
  if (result == lifecycle::AckResult::kReady) {
    finish_reset(vm);
  }
}

// Runs only on the VM boot VCPU's owner. Returns true when the caller's
// live frame belonged to a VCPU retired here and must be scheduled away
// after all mailbox requests in the current batch have been handled.
[[nodiscard]] auto begin_reset_local(std::size_t vm) noexcept -> bool {
  if (vm >= guest_table().size() || slot_cpu(slot_of(vm)) != cpu::id()) {
    release_reset(vm);
    return false;
  }

  std::uint32_t live_mask = 0;
  for (std::size_t v = 0; v < guest_table()[vm].vcpus; ++v) {
    if (vcpu::vcpu_on(slot_of(vm, v))) {
      live_mask |= std::uint32_t{1} << v;
    }
  }

  auto&      tracker = g_reset[vm];
  const auto plan    = tracker.begin(live_mask);
  if (!plan.accepted) {
    tracker.cancel();
    release_reset(vm);
    return false;
  }
  console::write("[smp] VM ");
  console::write_dec64(vm);
  console::write(" reset epoch ");
  console::write_dec64(plan.epoch);
  console::write(" pending mask 0x");
  console::write_hex64(plan.pending_mask);
  console::write("\n");

  // Validate every foreign owner before sending anything. Once one
  // quiesce command is visible, cancellation may stop only part of a VM.
  for (std::size_t v = 0; v < guest_table()[vm].vcpus; ++v) {
    if ((live_mask & (std::uint32_t{1} << v)) == 0U) {
      continue;
    }
    const std::size_t owner = slot_cpu(slot_of(vm, v));
    if (owner != cpu::id() && (owner >= g_online.size() || !g_online[owner].load(std::memory_order_acquire))) {
      tracker.cancel();
      release_reset(vm);
      console::write("[smp] reset rejected: target core offline\n");
      return false;
    }
  }

  for (std::size_t v = 0; v < guest_table()[vm].vcpus; ++v) {
    if ((live_mask & (std::uint32_t{1} << v)) == 0U) {
      continue;
    }
    const std::size_t slot  = slot_of(vm, v);
    const std::size_t owner = slot_cpu(slot);
    if (owner != cpu::id() &&
        !enqueue(owner, {.op = Op::kQuiesceVcpu, .idx = static_cast<std::uint32_t>(slot), .a = plan.epoch, .b = 0},
                 true)) {
      tracker.cancel();
      release_reset(vm);
      console::write("[smp] reset rejected: quiesce mailbox full\n");
      return false;
    }
  }

  bool schedule_required = false;
  for (std::size_t v = 0; v < guest_table()[vm].vcpus; ++v) {
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
    finish_reset(vm);
  }
  return schedule_required;
}

} // namespace

void start_secondaries() noexcept {
  const auto entry = reinterpret_cast<std::uint64_t>(&nova_secondary_entry);

  g_online[0].store(true, std::memory_order_release);
  gic::enable_ppi(kCrossCallSgi); // the primary receives cross-calls too

  for (std::size_t i = 1; i < cpu::kMaxCpus; ++i) {
    const std::uint64_t ret = arch::smc_call(PSCI_FN_CPU_ON | PSCI_FN_SMC64, i, entry, i);
    if (ret != PSCI_SUCCESS) {
      console::write("[smp] core ");
      console::write_dec64(i);
      console::write(" CPU_ON failed — continuing without it\n");
      continue;
    }

    const std::uint64_t deadline = hyp_timer::now() + hyp_timer::freq() * kOnlineWaitMs / 1000U;
    while (!g_online[i].load(std::memory_order_acquire) && hyp_timer::now() < deadline) {
      // secondary is booting
    }
    if (!g_online[i].load(std::memory_order_acquire)) {
      console::write("[smp] core ");
      console::write_dec64(i);
      console::write(" did not come online\n");
    }
  }

  // With every core online, bring up the VMs configured to boot on
  // their own — unmodified guest OSes never issue HVC_VM_START for
  // their neighbors. VM 0 already boots via the scheduler init;
  // foreign-affinity VMs go through the regular cross-call path.
  for (std::size_t vm = 1; vm < guest_table().size(); ++vm) {
    if (guest_table()[vm].auto_start && !start_vm(vm)) {
      console::write("[smp] VM ");
      console::write_dec64(vm);
      console::write(" autostart failed\n");
    }
  }
}

auto start_vm(std::size_t vm) noexcept -> bool {
  if (vm >= guest_table().size() || g_reset_active[vm].load(std::memory_order_acquire)) {
    return false;
  }
  const std::size_t boot = slot_of(vm);
  if (slot_cpu(boot) == cpu::id()) {
    return vcpu::start_vm(vm);
  }
  return enqueue(slot_cpu(boot), {.op = Op::kStartVm, .idx = static_cast<std::uint32_t>(vm), .a = 0, .b = 0});
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

auto cpu_on(std::size_t slot, std::uint64_t entry, std::uint64_t context_id) noexcept -> bool {
  if (!valid_slot(slot) || g_reset_active[vm_of(slot)].load(std::memory_order_acquire)) {
    return false;
  }
  if (slot_cpu(slot) == cpu::id()) {
    return vcpu::start_vcpu(slot, entry, context_id);
  }
  return enqueue(slot_cpu(slot),
                 {.op = Op::kCpuOn, .idx = static_cast<std::uint32_t>(slot), .a = entry, .b = context_id});
}

void stop_vm(std::size_t vm, TrapContext* live) noexcept {
  // Stop every live vCPU except the caller's own, then the local one
  // last — stopping a resident vCPU schedules away through `live`, so
  // nothing may follow it on this path.
  const std::size_t self = vcpu::current_index();
  for (std::size_t v = 0; v < guest_table()[vm].vcpus; ++v) {
    const std::size_t slot = slot_of(vm, v);
    if (slot == self || !vcpu::vcpu_on(slot)) {
      continue;
    }
    if (slot_cpu(slot) == cpu::id()) {
      vcpu::stop_vcpu(slot, live);
    } else {
      (void)enqueue(slot_cpu(slot), {.op = Op::kStopVcpu, .idx = static_cast<std::uint32_t>(slot), .a = 0, .b = 0});
    }
  }
  if (vm_of(self) == vm) {
    vcpu::stop_vcpu(self, live);
  }
}

void reset_vm(std::size_t vm, TrapContext* live, bool from_irq) noexcept {
  if (vm >= guest_table().size() || !vcpu::vm_on(vm)) {
    return;
  }

  bool expected = false;
  if (!g_reset_active[vm].compare_exchange_strong(expected, true, std::memory_order_acq_rel)) {
    return; // a reset for this VM is already in flight
  }
  vcpu::begin_lifecycle_transition();

  const std::size_t boot = slot_of(vm);
  if (slot_cpu(boot) == cpu::id()) {
    if (begin_reset_local(vm)) {
      if (from_irq) {
        core_gic::defer_epilogue(&vcpu::schedule_after_retire);
      } else {
        vcpu::schedule_after_retire(live);
      }
    }
    return;
  }

  if (!enqueue(slot_cpu(boot), {.op = Op::kBeginReset, .idx = static_cast<std::uint32_t>(vm), .a = 0, .b = 0}, true)) {
    release_reset(vm);
    console::write("[smp] reset rejected: coordinator mailbox unavailable\n");
    return;
  }

  // SYSTEM_RESET must not return to a secondary caller while the boot
  // owner coordinates the VM. Publish it off after the begin request is
  // visible; the coordinator's live mask will then exclude it.
  const std::size_t self = vcpu::current_index();
  if (self < kMaxVcpus && vm_of(self) == vm && vcpu::retire_vcpu(self)) {
    if (from_irq) {
      core_gic::defer_epilogue(&vcpu::schedule_after_retire);
    } else {
      vcpu::schedule_after_retire(live);
    }
  }
}

} // namespace nova::smp

namespace nova {

void smp_component::handle_hvc(HvcCall* call) noexcept {
  if (call->func_id != NOVA_HVC_FN_VM_START) {
    return; // not ours — leave unclaimed for other subscribers
  }
  call->handled   = true;
  call->ctx->x[0] = smp::start_vm(static_cast<std::size_t>(call->ctx->x[1])) ? 0 : kSmcccNotSupported;
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
  if (call->intid != smp::kCrossCallSgi) {
    return;
  }
  call->handled = true;

  // Copy the batch out first — executing under the lock would deadlock
  // against a sender targeting this core from another IRQ path.
  smp::Mailbox&                                   box = smp::g_mail[cpu::id()];
  std::array<smp::Request, smp::kMailboxCapacity> batch{};
  std::size_t                                     n = 0;
  {
    sync::Guard guard{box.lock};
    n         = box.count;
    box.count = 0;
    batch     = box.req;
  }
  bool schedule_required = false;
  for (std::size_t i = 0; i < n; ++i) {
    const smp::Request& r = batch[i];
    switch (r.op) {
    case smp::Op::kStartVm:
      (void)vcpu::start_vm(r.idx); // owner's verdict; the requester was told "accepted"
      break;
    case smp::Op::kPostVirq:
      (void)vcpu::post_virq(r.idx, static_cast<std::uint32_t>(r.a));
      break;
    case smp::Op::kCpuOn:
      (void)vcpu::start_vcpu(r.idx, r.a, r.b);
      break;
    case smp::Op::kStopVcpu:
      schedule_required = vcpu::retire_vcpu(r.idx) || schedule_required;
      break;
    case smp::Op::kBeginReset:
      schedule_required = smp::begin_reset_local(r.idx) || schedule_required;
      break;
    case smp::Op::kQuiesceVcpu: {
      schedule_required       = vcpu::retire_vcpu(r.idx) || schedule_required;
      const std::size_t owner = smp::slot_cpu(slot_of(vm_of(r.idx)));
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
  if (schedule_required) {
    core_gic::defer_epilogue(&vcpu::schedule_after_retire);
  }
}

} // namespace nova

// C entry for secondaries (from boot.S nova_secondary_entry, on this
// core's own stack, vectors installed). Brings up everything banked
// per PE, reports online, and enters this core's scheduler.
extern "C" [[noreturn]] void novavisor_secondary(std::uint64_t cpu_index) noexcept {
  using namespace nova;

  gic::init_cpu();                               // redistributor + ICC
  vgic::init_cpu();                              // ICH + maintenance PPI
  hyp_timer::init();                             // CNTHCTL/CNTVOFF/CNTHP
  soft_timer::init();                            // CNTHP PPI enable
  gic::enable_ppi(hyp_timer::kGuestTimerVintid); // native guest CNTV
  gic::enable_ppi(smp::kCrossCallSgi);           // cross-call mailbox
  mmu::activate_cpu();                           // VTCR/HCR — Stage 2 for this PE

  console::write("[smp] core ");
  console::write_dec64(cpu_index);
  console::write(" online\n");
  smp::g_online[cpu_index].store(true, std::memory_order_release);

  vcpu::enter_cpu();
}
