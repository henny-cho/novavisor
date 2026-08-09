// components/core_vcpu/src/sched.cpp
//
// VCPU scheduler. A switch swaps the live EL2 trap frame with the
// target's saved TrapContext, moves the EL1 sysreg bank
// (hal/vcpu_context.hpp) and the vGIC CPU-interface state,
// and retargets VTTBR_EL2 — the common vec.S restore path then resumes
// the new guest. Pick/predicate decisions live in sched_model.hpp
// (pure, host-tested); this file is the hardware glue.
//
// SMP ownership rule: a VCPU runs on its static affinity core and ALL
// of its state (ctx/EL1/FP banks, run state, timer slots) is read and
// written only there. Cross-core requests arrive as local calls through
// the smp component's cross-call path. Detailed scheduler state stays
// owner-local; other cores observe only atomic published snapshots.

#include "console_mux/console_mux.hpp"
#include "core_gic/core_gic.hpp"
#include "core_mmu/core_mmu.hpp"
#include "core_vcpu/core_vcpu.hpp"
#include "hal/console.hpp"
#include "hal/cpu.hpp"
#include "hal/timer.hpp"
#include "nova/abi/guest.hpp"
#include "nova/abi/hvc_abi.h"
#include "nova/panic.hpp"
#include "soft_timer/soft_timer.hpp"
#include "trace/trace.hpp"
#include "vcpu_internal.hpp"
#include "vgic/vgic.hpp"
#include "vgic/vgic_model.hpp"

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <span>

namespace nova {

// Defined in hal/arch/aarch64/guest/vcpu_enter.S. x0_arg is the guest's boot
// argument (PSCI CPU_ON context_id) — the only seeded GP register the
// first entry must carry; the rest are zeroed.
extern "C" [[noreturn]] void nova_vcpu_enter(std::uint64_t entry_pc, std::uint64_t sp_el1, std::uint64_t spsr_el2,
                                             std::uint64_t x0_arg) noexcept;

namespace vcpu {

std::array<Vcpu, kMaxVcpus>         g_vcpus;
std::atomic<std::uint64_t>          g_slice_ticks{0};
std::array<CpuSched, cpu::kMaxCpus> g_sched;

bool g_scheduler_started = false;

std::atomic<std::size_t> g_alive{0};
std::atomic<bool>        g_halt_announced{false};

// Every FP-trap write funnels through these so the per-core mirror
// stays truthful. seed writes unconditionally (boot and idle entry —
// hardware may hold anything); the switch path skips a matching value
// and lets the ERET synchronize the change.
void seed_fp_trap(bool trap) noexcept {
  arch::set_fp_trap(trap);
  me().fp_trap = trap;
}

namespace {

void switch_fp_trap(bool trap) noexcept {
  CpuSched& cs = me();
  if (cs.fp_trap != trap) {
    arch::set_fp_trap_before_eret(trap);
    cs.fp_trap = trap;
  }
}

// This core's view of the slot table: vCPUs with a foreign affinity
// are masked as kOff, so the pure scheduler model needs no affinity
// notion.
auto states() noexcept -> std::array<sched::State, kMaxVcpus> {
  const auto                          self = static_cast<std::uint8_t>(cpu::id());
  std::array<sched::State, kMaxVcpus> s{};
  for (std::size_t i = 0; i < g_count; ++i) {
    s[i] = g_affinity[i] == self ? g_vcpus[i].state : sched::State::kOff;
  }
  return s;
}

auto pick_next() noexcept -> std::size_t {
  const auto s = states();
  // kNoVcpu + 1 wraps to 0: an idle core scans the ring from the top.
  return sched::pick_next(std::span{s.data(), g_count}, me().current);
}

// Swap the resident VCPU: park the outgoing guest's state (trap frame,
// EL1 bank, vGIC CPU interface), load the incoming one, retarget
// Stage 2. The caller returns through vec.S which restores *live — now
// the new guest. The outgoing state survives as set by the caller
// (kBlocked/kOff); a still-running one becomes kReady.
void switch_to(TrapContext* live, std::size_t next_idx) noexcept {
  CpuSched& cs   = me();
  Vcpu&     next = g_vcpus[next_idx];
  trace_emit(NOVA_TRACE_EV_SCHED_SWITCH, static_cast<std::uint32_t>(next_idx), cs.current);

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
  cpu::write_vmpidr(vcpu_of(next_idx));
  hyp_timer::write_cntvoff(cntvoff(vm_of(next_idx)));

  // Lazy FP: the register file stays put — only the trap follows the
  // resident. A non-owner claims it through the EC 0x07 path on first
  // use; the owner keeps running untrapped. The common no-change case
  // skips the CPTR write; the ERET synchronizes an actual change.
  switch_fp_trap(cs.fp.trap_needed(next_idx));

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

    if (g_alive.load(std::memory_order_acquire) == 0 && g_lifecycle_transitions.load(std::memory_order_acquire) == 0 &&
        !g_halt_announced.exchange(true)) {
      console::write("[core_vcpu] all VCPUs off — halting\n");
      halt();
    }

    // Mark the idle window so a drain epilogue that retires state does
    // not recurse into a nested scheduler (each nesting level would
    // stack another idle frame) — this loop's own re-check picks up
    // whatever the handlers changed.
    me().idling = true;
    __asm__ volatile("wfi");
    core_gic::drain(live);
    me().idling = false;
    // An IRQ handler can retire the old resident and install a fresh
    // running context (reset quiesce followed by CPU_ON). Unwind this
    // idle frame instead of parking the new running context again.
    if (me().current != kNoVcpu && g_vcpus[me().current].state == sched::State::kRunning) {
      return;
    }
  }
}

} // namespace

// Keep the preemption slice armed exactly while the resident VCPU has
// a runnable competitor. Re-evaluated at every ready-set change:
// switch-in, wake, VM start, and slice expiry itself. An idle core
// (no resident) arms nothing — its entry loop schedules directly.
void reschedule_slice() noexcept {
  const auto s = states();
  if (me().current != kNoVcpu && sched::slice_needed(std::span{s.data(), g_count})) {
    const std::uint64_t ticks = g_slice_ticks.load(std::memory_order_relaxed);
    soft_timer::arm(soft_timer::kSlotSlice, hyp_timer::now() + ticks, &on_slice, 0);
  } else {
    soft_timer::cancel(soft_timer::kSlotSlice);
  }
}

// Move the preemption quantum, machine-wide.
//
// Takes effect where the quantum is armed: this core re-arms now, and a
// peer picks the new value up at its next ready-set change — the same
// moment it would have read the old one. Nothing already armed is
// shortened, so a guest mid-slice keeps the turn it was given.
auto slice_band() noexcept -> SliceBand {
  // Narrowing on purpose: these are constant expressions, so a band the
  // published field cannot hold stops the build here.
  return {.min_us = kSliceMinUs, .default_us = kSliceUs, .max_us = kSliceMaxUs};
}

auto set_slice_us(std::uint64_t microseconds) noexcept -> bool {
  if (microseconds < kSliceMinUs || microseconds > kSliceMaxUs) {
    return false;
  }
  const auto plan = arch::us_to_ticks(hyp_timer::freq(), microseconds);
  if (!plan.accepted) {
    return false;
  }
  g_slice_ticks.store(plan.ticks, std::memory_order_relaxed);
  reschedule_slice();
  return true;
}

auto current() noexcept -> Vcpu& {
  return g_vcpus[me().current];
}

auto current_index() noexcept -> std::size_t {
  return me().current;
}

[[noreturn]] void enter_cpu() noexcept {
  g_scheduler_started = true;
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
      cpu::write_vmpidr(vcpu_of(next));
      hyp_timer::write_cntvoff(cntvoff(vm_of(next)));
      arch::write_el1_bank(v.el1);
      vgic::cpu_restore(next);
      seed_fp_trap(cs.fp.trap_needed(next)); // unconditional: a secondary's CPTR is untouched until here
      v.state    = sched::State::kRunning;
      cs.current = next;
      reschedule_slice();
      nova_vcpu_enter(v.ctx.elr, v.ctx.sp, v.ctx.spsr, v.ctx.x[0]);
    }
    me().idling = true;
    __asm__ volatile("wfi");
    core_gic::drain(&idle);
    me().idling = false;
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
  // timer into a soft-timer wake-up at the same absolute deadline.
  // CVAL is virtual time; the CNTHP comparator is physical — re-base
  // through the VM's CNTVOFF.
  const std::uint64_t ctl = hyp_timer::guest_cntv_ctl();
  if ((ctl & hyp_timer::kCntCtlEnable) != 0 && (ctl & hyp_timer::kCntCtlImask) == 0) {
    soft_timer::arm(soft_timer::kSlotCntvWake + self, hyp_timer::guest_cntv_cval() + cntvoff(vm_of(self)),
                    &on_cntv_wake, static_cast<std::uint64_t>(self));
  }

  schedule_out(live);
}

void wake(std::size_t index) noexcept {
  if (!valid_slot(index) || g_vcpus[index].state != sched::State::kBlocked) {
    return;
  }
  g_vcpus[index].state = sched::State::kReady;
  soft_timer::cancel(soft_timer::kSlotCntvWake + index);
  reschedule_slice(); // the resident VCPU just gained a competitor
}

void schedule_after_retire(TrapContext* live) noexcept {
  if (me().idling) {
    return; // already inside the idle loop — its own re-check takes over (no nested scheduler)
  }
  schedule_out(live);
}

auto post_virq(std::size_t slot, std::uint32_t vintid) noexcept -> bool {
  if (slot >= g_count || affinity(slot) != cpu::id()) {
    return false; // foreign-affinity posts arrive through the smp cross-call
  }
  if (vintid >= vgic::kNumPrivate) {
    // SPI: VM-global state — the vGIC resolves the route with the
    // pending update and the reevaluate fan-out wakes the routed
    // target (which may be a different vCPU than `slot`).
    return vgic::post_spi(vm_of(slot), vintid);
  }
  if (g_vcpus[slot].state == sched::State::kOff) {
    return false; // private state belongs to a powered-on vCPU
  }
  if (!vgic::post_private(slot, vintid)) {
    return false;
  }
  // Wake a blocked target only when the vGIC would actually signal it
  // — a pended-but-disabled INTID is not a wfi wake-up event.
  if (g_vcpus[slot].state == sched::State::kBlocked && vgic::has_deliverable(slot)) {
    wake(slot);
  }
  return true;
}

void reevaluate_virq(std::size_t slot) noexcept {
  if (!valid_slot(slot) || affinity(slot) != cpu::id() || g_vcpus[slot].state == sched::State::kOff) {
    return;
  }
  if (vgic::reevaluate(slot) && g_vcpus[slot].state == sched::State::kBlocked) {
    wake(slot);
  }
}

auto vcpu_on(std::size_t slot) noexcept -> bool {
  return power_state(slot) != PowerState::kOff;
}

auto power_state(std::size_t slot) noexcept -> PowerState {
  return valid_slot(slot) ? published_power(slot) : PowerState::kOff;
}

} // namespace vcpu

void core_vcpu_component::handle_fp_simd(FpSimdCall* call) noexcept {
  call->handled = true;

  // Make FP access legal first (ISB inside) — the bank moves below run
  // at EL2 and would self-trap otherwise.
  vcpu::seed_fp_trap(false);

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
  if (vgic::reevaluate(vcpu::current_index())) {
    return; // pending wake-up event → architecturally a NOP
  }
  // A guest idling for input often parks with an unterminated line
  // buffered (a shell prompt) — emit it so the console shows the
  // prompt instead of holding it until the next newline.
  console_mux::flush(vcpu::current_index());
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
