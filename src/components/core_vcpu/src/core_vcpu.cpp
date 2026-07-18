// components/core_vcpu/src/core_vcpu.cpp
//
// VCPU scheduler. A switch swaps the live EL2 trap frame with the
// target's saved TrapContext, moves the EL1 sysreg bank
// (hal/arch/aarch64/el1_context.hpp) and the vGIC CPU-interface state,
// and retargets VTTBR_EL2 — the common vec.S restore path then resumes
// the new guest. Pick/predicate decisions live in sched_model.hpp
// (pure, host-tested); this file is the hardware glue.

#include "core_vcpu/core_vcpu.hpp"

#include "core_gic/core_gic.hpp"
#include "core_mmu/core_mmu.hpp"
#include "hal/console.hpp"
#include "hal/timer.hpp"
#include "nova/abi/guest.hpp"
#include "nova/abi/hvc_abi.h"
#include "nova_panic/nova_panic.hpp"
#include "soft_timer/soft_timer.hpp"
#include "vgic/vgic.hpp"

#include <array>
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

std::array<Vcpu, kMaxGuests>         g_vcpus;
std::size_t                          g_count       = 0;
std::size_t                          g_current     = 0;
std::uint64_t                        g_slice_ticks = 0;
fp::Ownership                        g_fp;
lifecycle::RestartBudget<kMaxGuests> g_budget;

auto states() noexcept -> std::array<sched::State, kMaxGuests> {
  std::array<sched::State, kMaxGuests> s{};
  for (std::size_t i = 0; i < g_count; ++i) {
    s[i] = g_vcpus[i].state;
  }
  return s;
}

auto pick_next() noexcept -> std::size_t {
  const auto s = states();
  return sched::pick_next(std::span{s.data(), g_count}, g_current);
}

void reschedule_slice() noexcept;

// Reset a VCPU to its descriptor's boot state. GP registers are zeroed
// so no EL2 (or previous-guest) values leak into a fresh guest.
void seed(std::size_t index, const GuestDescriptor& guest) noexcept {
  Vcpu& v    = g_vcpus[index];
  v.guest    = &guest;
  v.ctx      = TrapContext{};
  v.ctx.elr  = guest.entry_pc;
  v.ctx.sp   = guest.stack_top;
  v.ctx.spsr = kSpsrEl1h;
  v.el1      = arch::El1SysregBank{};
  v.fp       = arch::FpBank{};
  // Whatever this VCPU owned in the hardware FP file is garbage now —
  // drop ownership so no one ever saves it over a fresh bank.
  g_fp.invalidate(index);
  vgic::cpu_reset(index);
  // A reseeded guest owes nothing to its past life: drop the parked-CNTV
  // wake-up mirror (a fresh bank has CNTV disabled) and the heartbeat
  // deadline (the rebooted guest re-opts in with its next heartbeat).
  soft_timer::cancel(soft_timer::kSlotCntvWake + index);
  soft_timer::cancel(soft_timer::kSlotWatchdog + index);
}

// Swap the resident VCPU: park the outgoing guest's state (trap frame,
// EL1 bank, vGIC CPU interface), load the incoming one, retarget
// Stage 2. The caller returns through vec.S which restores *live — now
// the new guest. The outgoing state survives as set by the caller
// (kBlocked/kOff); a still-running one becomes kReady.
void switch_to(TrapContext* live, std::size_t next_idx) noexcept {
  Vcpu& cur  = g_vcpus[g_current];
  Vcpu& next = g_vcpus[next_idx];

  cur.ctx = *live;
  cur.el1 = arch::read_el1_bank();
  vgic::cpu_save(g_current);
  if (cur.state == sched::State::kRunning) {
    cur.state = sched::State::kReady;
  }

  *live = next.ctx;
  arch::write_el1_bank(next.el1);
  vgic::cpu_restore(next_idx);
  mmu::switch_vm(next_idx);

  // Lazy FP: the register file stays put — only the trap follows the
  // resident. A non-owner claims it through the EC 0x07 path on first
  // use; the owner keeps running untrapped.
  arch::set_fp_trap(g_fp.trap_needed(next_idx));

  next.state = sched::State::kRunning;
  g_current  = next_idx;
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
// switch-in, wake, VM start, and slice expiry itself.
void reschedule_slice() noexcept {
  const auto s = states();
  if (sched::slice_needed(std::span{s.data(), g_count})) {
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
// and drain dispatches it (soft-timer wake, doorbell) without taking
// an exception. Halts once every VCPU has retired.
void schedule_out(TrapContext* live) noexcept {
  for (;;) {
    const std::size_t next = pick_next();
    if (next < g_count) {
      if (next == g_current) {
        // Woke itself while idling — resume without a frame swap.
        g_vcpus[g_current].state = sched::State::kRunning;
        reschedule_slice();
      } else {
        switch_to(live, next);
      }
      return;
    }

    const auto s = states();
    if (sched::all_off(std::span{s.data(), g_count})) {
      console::write("[core_vcpu] all VCPUs off — halting\n");
      halt();
    }

    __asm__ volatile("wfi");
    core_gic::drain(live);
  }
}

} // namespace

auto current() noexcept -> Vcpu& {
  return g_vcpus[g_current];
}

auto current_index() noexcept -> std::size_t {
  return g_current;
}

void init() noexcept {
  const auto guests = guest_table();
  g_count           = guests.size() <= kMaxGuests ? guests.size() : kMaxGuests; // core_mmu already panicked if over
  for (std::size_t i = 0; i < g_count; ++i) {
    seed(i, guests[i]);
  }
  g_vcpus[0].state = sched::State::kReady;
  g_slice_ticks    = hyp_timer::freq() * kSliceMs / 1000U;
  arch::set_fp_trap(true); // no owner yet — first FP use claims the file
}

[[noreturn]] void enter_guest() noexcept {
  Vcpu& boot = g_vcpus[0];
  boot.state = sched::State::kRunning;
  g_current  = 0;
  nova_vcpu_enter(boot.ctx.elr, boot.ctx.sp, boot.ctx.spsr);
}

void yield_current(TrapContext* live) noexcept {
  const std::size_t next = pick_next();
  if (next < g_count && next != g_current) {
    switch_to(live, next);
  }
}

void block_current(TrapContext* live) noexcept {
  g_vcpus[g_current].state = sched::State::kBlocked;

  // The resident CNTV is live in hardware, but once parked in the EL1
  // bank it can never meet its condition — mirror an armed, unmasked
  // timer into a soft-timer wake-up at the same absolute deadline
  // (CNTVOFF = 0, so CVAL and the CNTHP comparator share a domain).
  const std::uint64_t ctl = hyp_timer::guest_cntv_ctl();
  if ((ctl & hyp_timer::kCntCtlEnable) != 0 && (ctl & hyp_timer::kCntCtlImask) == 0) {
    soft_timer::arm(soft_timer::kSlotCntvWake + g_current, hyp_timer::guest_cntv_cval(), &on_cntv_wake,
                    static_cast<std::uint64_t>(g_current));
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

auto start_vm(std::size_t index) noexcept -> bool {
  if (index >= g_count || g_vcpus[index].state != sched::State::kOff) {
    return false;
  }
  seed(index, guest_table()[index]);
  g_budget.refill(index); // cold start — fresh warm-reset budget
  g_vcpus[index].state = sched::State::kReady;
  reschedule_slice(); // the resident VCPU just gained a competitor
  return true;
}

void reset_vm(std::size_t index, TrapContext* live) noexcept {
  if (index >= g_count || g_vcpus[index].state == sched::State::kOff) {
    return; // stopped VMs are revived by start_vm only
  }

  if (!g_budget.take(index)) {
    console::write("[core_vcpu] VM ");
    console::write_dec64(index);
    console::write(" restart budget exhausted — stopping\n");
    g_vcpus[index].state = sched::State::kOff;
    soft_timer::cancel(soft_timer::kSlotWatchdog + index); // no reset from beyond the grave
    if (index == g_current) {
      schedule_out(live);
    }
    return;
  }

  mmu::reload_guest_image(index);
  seed(index, guest_table()[index]);

  Vcpu& v = g_vcpus[index];
  if (index == g_current) {
    // Reset in place: load the fresh context straight into the live
    // frame and keep running — switch_to would save the live frame
    // over the seed. Stage 2 already targets this VM.
    *live = v.ctx;
    arch::write_el1_bank(v.el1);
    vgic::cpu_restore(index);
    arch::set_fp_trap(g_fp.trap_needed(index));
    v.state = sched::State::kRunning;
  } else {
    v.state = sched::State::kReady;
  }
  reschedule_slice();
}

void exit_current(TrapContext* live) noexcept {
  g_vcpus[g_current].state = sched::State::kOff;
  // A stopped VM must never be watchdog-reset back to life.
  soft_timer::cancel(soft_timer::kSlotWatchdog + g_current);
  schedule_out(live);
}

auto post_virq(std::size_t index, std::uint32_t vintid) noexcept -> bool {
  if (index >= g_count || g_vcpus[index].state == sched::State::kOff) {
    return false;
  }
  if (!vgic::post(index, vintid)) {
    return false;
  }
  // Wake a blocked target only when the vGIC would actually signal it
  // — a pended-but-disabled INTID is not a wfi wake-up event.
  if (g_vcpus[index].state == sched::State::kBlocked && vgic::has_deliverable(index)) {
    wake(index);
  }
  return true;
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
  const std::size_t prev = vcpu::g_fp.claim(cur);
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
  case NOVA_HVC_FN_VM_START:
    call->handled   = true;
    call->ctx->x[0] = vcpu::start_vm(static_cast<std::size_t>(call->ctx->x[1])) ? 0 : kSmcccNotSupported;
    return;
  default:
    return; // not ours — leave unclaimed for other subscribers
  }
}

} // namespace nova
