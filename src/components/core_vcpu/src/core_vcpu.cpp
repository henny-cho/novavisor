// components/core_vcpu/src/core_vcpu.cpp
//
// VCPU scheduler. A switch swaps the live EL2 trap frame with the
// target's saved TrapContext, moves the EL1 sysreg bank
// (hal/arch/aarch64/el1_context.hpp) and the vGIC CPU-interface state,
// and retargets VTTBR_EL2 — the common vec.S restore path then resumes
// the new guest. Pick/predicate decisions live in sched_model.hpp
// (pure, host-tested); this file is the hardware glue.
//
// SMP ownership rule: a VCPU runs on its static affinity core and ALL
// of its state (ctx/EL1/FP banks, run state, timer slots) is read and
// written only there. Cross-core requests arrive as local calls
// through the smp component's cross-call path, so nothing here needs
// a lock; the only cross-core data is the atomic alive counter.
//
// Index model: every entry point takes a flat vCPU slot
// (nova/abi/guest.hpp slot math). Per-VM state — Stage 2, the restart
// budget, the watchdog deadline — keys on vm_of(slot) and is owned by
// vcpu 0's affinity core.

#include "core_vcpu/core_vcpu.hpp"

#include "core_gic/core_gic.hpp"
#include "core_mmu/core_mmu.hpp"
#include "hal/arch/aarch64/cpu.hpp"
#include "hal/console.hpp"
#include "hal/cpu.hpp"
#include "hal/timer.hpp"
#include "nova/abi/guest.hpp"
#include "nova/abi/hvc_abi.h"
#include "nova_panic/nova_panic.hpp"
#include "soft_timer/soft_timer.hpp"
#include "vgic/vgic.hpp"

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <span>

namespace nova {

// Defined in hal/arch/aarch64/vcpu_enter.S.
extern "C" [[noreturn]] void nova_vcpu_enter(std::uint64_t entry_pc, std::uint64_t sp_el1,
                                             std::uint64_t spsr_el2) noexcept;

namespace vcpu {
namespace {

// SPSR_EL2 value to restore on ERET into a fresh guest:
//   M[3:0]   = 0b0101  EL1h  (EL1, using SP_EL1)
//   M[4]     = 0       AArch64 execution state
//   F (b6)   = 1       FIQ masked
//   I (b7)   = 1       IRQ masked
//   A (b8)   = 1       SError masked
//   D (b9)   = 1       Debug masked
//   others   = 0
// The guest unmasks I itself once its vector table is installed.
inline constexpr std::uint64_t kSpsrEl1h = 0x3C5ULL;

// Preemption quantum. Converted to counter ticks at init from the
// platform clock; board-specific tuning waits for a second board
// (single-source trigger discipline).
inline constexpr std::uint64_t kSliceMs = 10;

// "No VCPU resident on this core" — before the first guest entry and
// after every local guest retired.
inline constexpr std::size_t kNoVcpu = ~std::size_t{0};

// Per-core scheduler state: the resident VCPU and the ownership of
// this core's FP register file.
struct CpuSched {
  std::size_t   current = kNoVcpu;
  fp::Ownership fp;
};

std::array<Vcpu, kMaxVcpus>          g_vcpus;
std::size_t                          g_count       = 0; // vCPU slots; boot-immutable after init()
std::uint64_t                        g_slice_ticks = 0; // boot-immutable after init()
std::array<CpuSched, cpu::kMaxCpus>  g_sched;
lifecycle::RestartBudget<kMaxGuests> g_budget; // per-VM — micro-reboot is a VM-level policy

// vCPUs not yet retired, machine-wide — the only scheduler state
// shared across cores. Each core idles on its own empty ready-set; the
// halt line is printed exactly once, by whichever core retires the
// last vCPU.
std::atomic<std::size_t> g_alive{0};
std::atomic<bool>        g_halt_announced{false};

[[nodiscard]] auto me() noexcept -> CpuSched& {
  return g_sched[cpu::id()];
}

[[nodiscard]] auto affinity(std::size_t slot) noexcept -> std::size_t {
  return guest_table()[vm_of(slot)].cpu[vcpu_of(slot)];
}

// Slots past a VM's vcpu count exist in the arrays but are never
// seeded, started, or picked — they stay kOff for the machine's life.
[[nodiscard]] auto valid_slot(std::size_t slot) noexcept -> bool {
  return slot < g_count && vcpu_of(slot) < guest_table()[vm_of(slot)].vcpus;
}

// This core's view of the slot table: vCPUs with a foreign affinity
// are masked as kOff, so the pure scheduler model needs no affinity
// notion.
auto states() noexcept -> std::array<sched::State, kMaxVcpus> {
  const std::size_t                   self = cpu::id();
  std::array<sched::State, kMaxVcpus> s{};
  for (std::size_t i = 0; i < g_count; ++i) {
    s[i] = affinity(i) == self ? g_vcpus[i].state : sched::State::kOff;
  }
  return s;
}

auto pick_next() noexcept -> std::size_t {
  const auto s = states();
  // kNoVcpu + 1 wraps to 0: an idle core scans the ring from the top.
  return sched::pick_next(std::span{s.data(), g_count}, me().current);
}

void reschedule_slice() noexcept;

// Reset a vCPU to a fresh execution context at `entry`. GP registers
// are zeroed so no EL2 (or previous-guest) values leak into it; x0
// carries `arg` (PSCI CPU_ON context_id — zero for a descriptor boot).
void seed(std::size_t slot, std::uint64_t entry, std::uint64_t sp, std::uint64_t arg) noexcept {
  Vcpu& v    = g_vcpus[slot];
  v.guest    = &guest_table()[vm_of(slot)];
  v.ctx      = TrapContext{};
  v.ctx.elr  = entry;
  v.ctx.sp   = sp;
  v.ctx.x[0] = arg;
  v.ctx.spsr = kSpsrEl1h;
  v.el1      = arch::El1SysregBank{};
  v.fp       = arch::FpBank{};
  // Whatever this vCPU owned in the hardware FP file is garbage now —
  // drop ownership so no one ever saves it over a fresh bank.
  g_sched[affinity(slot)].fp.invalidate(slot);
  vgic::cpu_reset(slot);
  // A reseeded vCPU owes nothing to its past life: drop the parked-CNTV
  // wake-up mirror (a fresh bank has CNTV disabled).
  soft_timer::cancel(soft_timer::kSlotCntvWake + slot);
}

// Descriptor boot state: vcpu 0's cold/warm entry. Secondary vCPUs are
// seeded by CPU_ON with a caller-supplied entry instead (SP is the
// guest's own business there — PSCI leaves it undefined).
void seed_boot(std::size_t slot) noexcept {
  const GuestDescriptor& guest = guest_table()[vm_of(slot)];
  seed(slot, guest.entry_pc, guest.stack_top, 0);
}

// True while any vCPU of `vm` has not retired.
[[nodiscard]] auto vm_has_live(std::size_t vm) noexcept -> bool {
  for (std::size_t v = 0; v < guest_table()[vm].vcpus; ++v) {
    if (g_vcpus[slot_of(vm, v)].state != sched::State::kOff) {
      return true;
    }
  }
  return false;
}

// Swap the resident VCPU: park the outgoing guest's state (trap frame,
// EL1 bank, vGIC CPU interface), load the incoming one, retarget
// Stage 2. The caller returns through vec.S which restores *live — now
// the new guest. The outgoing state survives as set by the caller
// (kBlocked/kOff); a still-running one becomes kReady.
void switch_to(TrapContext* live, std::size_t next_idx) noexcept {
  CpuSched& cs   = me();
  Vcpu&     next = g_vcpus[next_idx];

  if (cs.current != kNoVcpu) {
    Vcpu& cur = g_vcpus[cs.current];
    cur.ctx   = *live;
    cur.el1   = arch::read_el1_bank();
    vgic::cpu_save(cs.current);
    if (cur.state == sched::State::kRunning) {
      cur.state = sched::State::kReady;
    }
  }

  *live = next.ctx;
  arch::write_el1_bank(next.el1);
  vgic::cpu_restore(next_idx);
  mmu::switch_vm(vm_of(next_idx));
  arch::write_vmpidr(vcpu_of(next_idx));

  // Lazy FP: the register file stays put — only the trap follows the
  // resident. A non-owner claims it through the EC 0x07 path on first
  // use; the owner keeps running untrapped.
  arch::set_fp_trap(cs.fp.trap_needed(next_idx));

  next.state = sched::State::kRunning;
  cs.current = next_idx;
  reschedule_slice();
}

// Soft-timer callback: the mirrored CNTV deadline of a blocked VCPU
// passed — make it runnable. Injection happens naturally once it is
// resident again: the restored CNTV meets its condition and fires the
// physical PPI (single delivery path, no duplication).
void on_cntv_wake(TrapContext* /*ctx*/, std::uint64_t index) noexcept {
  wake(static_cast<std::size_t>(index));
}

void on_slice(TrapContext* ctx, std::uint64_t arg) noexcept;

// Keep the preemption slice armed exactly while the resident VCPU has
// a runnable competitor. Re-evaluated at every ready-set change:
// switch-in, wake, VM start, and slice expiry itself. An idle core
// (no resident) arms nothing — its entry loop schedules directly.
void reschedule_slice() noexcept {
  const auto s = states();
  if (me().current != kNoVcpu && sched::slice_needed(std::span{s.data(), g_count})) {
    soft_timer::arm(soft_timer::kSlotSlice, hyp_timer::now() + g_slice_ticks, &on_slice, 0);
  } else {
    soft_timer::cancel(soft_timer::kSlotSlice);
  }
}

// Slice expiry (runs in soft_timer's IRQ drain, HVC-identical frame
// swap): round-robin away from the resident VCPU.
void on_slice(TrapContext* ctx, std::uint64_t /*arg*/) noexcept {
  yield_current(ctx);
  reschedule_slice(); // yield may have found nobody — re-arm or park
}

// Leave the current VCPU as its caller marked it (kBlocked/kOff) and
// run the next runnable one. With nothing runnable, idle at EL2: wfi
// falls through on any pending physical IRQ even with PSTATE.I masked,
// and drain dispatches it (soft-timer wake, doorbell, cross-call)
// without taking an exception. The machine halts once every VM
// machine-wide has retired; a core whose own set is merely empty
// keeps idling — a cross-call may hand it new work.
void schedule_out(TrapContext* live) noexcept {
  for (;;) {
    const std::size_t next = pick_next();
    if (next < g_count) {
      if (next == me().current) {
        // Woke itself while idling — resume without a frame swap.
        g_vcpus[next].state = sched::State::kRunning;
        reschedule_slice();
      } else {
        switch_to(live, next);
      }
      return;
    }

    if (g_alive.load(std::memory_order_acquire) == 0 && !g_halt_announced.exchange(true)) {
      console::write("[core_vcpu] all VCPUs off — halting\n");
      halt();
    }

    __asm__ volatile("wfi");
    core_gic::drain(live);
  }
}

} // namespace

auto current() noexcept -> Vcpu& {
  return g_vcpus[me().current];
}

auto current_index() noexcept -> std::size_t {
  return me().current;
}

void init() noexcept {
  const auto        guests = guest_table();
  const std::size_t vms    = guests.size() <= kMaxGuests ? guests.size() : kMaxGuests; // core_mmu panicked if over
  g_count                  = vms * kMaxVcpusPerVm;
  for (std::size_t i = 0; i < g_count; ++i) {
    if (valid_slot(i)) {
      seed_boot(i);
    }
  }
  g_vcpus[slot_of(0)].state = sched::State::kReady;
  g_alive.store(1, std::memory_order_relaxed); // the boot guest's vcpu 0
  g_slice_ticks = hyp_timer::freq() * kSliceMs / 1000U;
  arch::set_fp_trap(true); // no owner yet — first FP use claims the file
}

[[noreturn]] void enter_cpu() noexcept {
  // Scratch frame for IRQ drain while no guest has ever run on this
  // core. Callbacks never frame-swap into it: with no resident VCPU
  // the slice is parked and yield is a no-op — a cross-call start
  // marks kReady and the pick below performs the first entry.
  TrapContext idle{};
  for (;;) {
    const std::size_t next = pick_next();
    if (next < g_count) {
      CpuSched& cs = me();
      Vcpu&     v  = g_vcpus[next];
      mmu::switch_vm(vm_of(next));
      arch::write_vmpidr(vcpu_of(next));
      arch::write_el1_bank(v.el1);
      vgic::cpu_restore(next);
      arch::set_fp_trap(cs.fp.trap_needed(next));
      v.state    = sched::State::kRunning;
      cs.current = next;
      reschedule_slice();
      nova_vcpu_enter(v.ctx.elr, v.ctx.sp, v.ctx.spsr);
    }
    __asm__ volatile("wfi");
    core_gic::drain(&idle);
  }
}

void yield_current(TrapContext* live) noexcept {
  if (me().current == kNoVcpu) {
    return; // idle core — nothing to yield away from
  }
  const std::size_t next = pick_next();
  if (next < g_count && next != me().current) {
    switch_to(live, next);
  }
}

void block_current(TrapContext* live) noexcept {
  const std::size_t self = me().current;
  g_vcpus[self].state    = sched::State::kBlocked;

  // The resident CNTV is live in hardware, but once parked in the EL1
  // bank it can never meet its condition — mirror an armed, unmasked
  // timer into a soft-timer wake-up at the same absolute deadline
  // (CNTVOFF = 0, so CVAL and the CNTHP comparator share a domain).
  const std::uint64_t ctl = hyp_timer::guest_cntv_ctl();
  if ((ctl & hyp_timer::kCntCtlEnable) != 0 && (ctl & hyp_timer::kCntCtlImask) == 0) {
    soft_timer::arm(soft_timer::kSlotCntvWake + self, hyp_timer::guest_cntv_cval(), &on_cntv_wake,
                    static_cast<std::uint64_t>(self));
  }

  schedule_out(live);
}

void wake(std::size_t index) noexcept {
  if (g_vcpus[index].state != sched::State::kBlocked) {
    return;
  }
  g_vcpus[index].state = sched::State::kReady;
  soft_timer::cancel(soft_timer::kSlotCntvWake + index);
  reschedule_slice(); // the resident VCPU just gained a competitor
}

auto start_vm(std::size_t vm) noexcept -> bool {
  const std::size_t slot = slot_of(vm);
  if (vm >= vm_of(g_count) || affinity(slot) != cpu::id() || g_vcpus[slot].state != sched::State::kOff) {
    return false; // foreign-affinity starts arrive through the smp cross-call
  }
  seed_boot(slot);
  g_budget.refill(vm); // cold start — fresh warm-reset budget
  g_vcpus[slot].state = sched::State::kReady;
  g_alive.fetch_add(1, std::memory_order_acq_rel);
  reschedule_slice(); // the resident VCPU just gained a competitor
  return true;
}

auto start_vcpu(std::size_t slot, std::uint64_t entry, std::uint64_t context_id) noexcept -> bool {
  if (!valid_slot(slot) || affinity(slot) != cpu::id() || g_vcpus[slot].state != sched::State::kOff) {
    return false; // foreign-affinity CPU_ON arrives through the smp cross-call
  }
  if (!vm_has_live(vm_of(slot))) {
    return false; // the VM itself has retired — nothing to join
  }
  seed(slot, entry, /*sp=*/0, context_id);
  g_vcpus[slot].state = sched::State::kReady;
  g_alive.fetch_add(1, std::memory_order_acq_rel);
  reschedule_slice();
  return true;
}

// Retire one vCPU (CPU_OFF, VM-wide stop fan-out). The VM's watchdog
// dies with its last vCPU — but only the deadline armed on THIS core's
// queue; a stale one elsewhere expires into a no-op (reset_vm ignores
// an all-off VM).
void stop_vcpu(std::size_t slot, TrapContext* live) noexcept {
  if (!valid_slot(slot) || affinity(slot) != cpu::id() || g_vcpus[slot].state == sched::State::kOff) {
    return;
  }
  g_vcpus[slot].state = sched::State::kOff;
  soft_timer::cancel(soft_timer::kSlotCntvWake + slot);
  if (!vm_has_live(vm_of(slot))) {
    soft_timer::cancel(soft_timer::kSlotWatchdog + vm_of(slot));
  }
  g_alive.fetch_sub(1, std::memory_order_acq_rel);
  if (slot == me().current) {
    schedule_out(live);
  }
}

void reset_vm(std::size_t vm, TrapContext* live) noexcept {
  const std::size_t slot = slot_of(vm);
  if (vm >= vm_of(g_count) || !vm_has_live(vm)) {
    return; // stopped VMs are revived by start_vm only
  }

  if (!g_budget.take(vm)) {
    console::write("[core_vcpu] VM ");
    console::write_dec64(vm);
    console::write(" restart budget exhausted — stopping\n");
    soft_timer::cancel(soft_timer::kSlotWatchdog + vm); // no reset from beyond the grave
    stop_vcpu(slot, live);                              // secondaries were stopped by the smp fan-out
    return;
  }

  mmu::reload_guest_image(vm);
  seed_boot(slot);
  soft_timer::cancel(soft_timer::kSlotWatchdog + vm); // the reboot re-opts in with its next heartbeat

  Vcpu& v = g_vcpus[slot];
  if (slot == me().current) {
    // Reset in place: load the fresh context straight into the live
    // frame and keep running — switch_to would save the live frame
    // over the seed. Stage 2 already targets this VM.
    *live = v.ctx;
    arch::write_el1_bank(v.el1);
    vgic::cpu_restore(slot);
    arch::write_vmpidr(vcpu_of(slot));
    arch::set_fp_trap(me().fp.trap_needed(slot));
    v.state = sched::State::kRunning;
  } else {
    v.state = sched::State::kReady;
  }
  reschedule_slice();
}

void exit_current(TrapContext* live) noexcept {
  stop_vcpu(me().current, live);
}

auto post_virq(std::size_t slot, std::uint32_t vintid) noexcept -> bool {
  if (slot >= g_count || affinity(slot) != cpu::id() || g_vcpus[slot].state == sched::State::kOff) {
    return false; // foreign-affinity posts arrive through the smp cross-call
  }
  if (!vgic::post(slot, vintid)) {
    return false;
  }
  // Wake a blocked target only when the vGIC would actually signal it
  // — a pended-but-disabled INTID is not a wfi wake-up event.
  if (g_vcpus[slot].state == sched::State::kBlocked && vgic::has_deliverable(slot)) {
    wake(slot);
  }
  return true;
}

auto vcpu_on(std::size_t slot) noexcept -> bool {
  // Cross-core read of a one-byte state — benign by construction (PSCI
  // AFFINITY_INFO is allowed a stale answer; the caller polls).
  return valid_slot(slot) && g_vcpus[slot].state != sched::State::kOff;
}

} // namespace vcpu

void core_vcpu_component::handle_guest_fault(GuestFaultCall* call) noexcept {
  call->handled = true;
  console::write("[core_vcpu] guest fault — stopping VCPU ");
  console::write_dec64(vcpu::current_index());
  console::write("\n");
  vcpu::exit_current(call->ctx);
}

void core_vcpu_component::handle_fp_simd(FpSimdCall* call) noexcept {
  call->handled = true;

  // Make FP access legal first (ISB inside) — the bank moves below run
  // at EL2 and would self-trap otherwise.
  arch::set_fp_trap(false);

  const std::size_t cur  = vcpu::current_index();
  const std::size_t prev = vcpu::g_sched[cpu::id()].fp.claim(cur);
  if (prev == cur) {
    return; // spurious — already the owner, state is already live
  }
  if (prev != fp::kNoOwner) {
    nova_fp_save(&vcpu::g_vcpus[prev].fp);
  }
  nova_fp_restore(&vcpu::g_vcpus[cur].fp);
}

void core_vcpu_component::handle_wfx(WfxCall* call) noexcept {
  call->handled = true;
  if (call->is_wfe) {
    vcpu::yield_current(call->ctx); // spin-wait hint — give the core away once
    return;
  }
  if (vgic::has_deliverable(vcpu::current_index())) {
    return; // pending wake-up event → architecturally a NOP
  }
  vcpu::block_current(call->ctx);
}

void core_vcpu_component::handle_hvc(HvcCall* call) noexcept {
  switch (call->func_id) {
  case NOVA_HVC_FN_YIELD:
    call->handled   = true;
    call->ctx->x[0] = 0; // written before the frame swap parks it
    vcpu::yield_current(call->ctx);
    return;
  default:
    return; // not ours — VM_START lives in smp (affinity routing)
  }
}

} // namespace nova
