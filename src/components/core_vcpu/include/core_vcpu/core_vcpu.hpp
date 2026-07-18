#pragma once

// components/core_vcpu/include/core_vcpu/core_vcpu.hpp
//
// VCPU state, scheduler, and EL1 entry component.
//
// One Vcpu per flat vCPU slot (nova/abi/guest.hpp: kMaxVcpusPerVm
// slots per guest_table() entry — a VM's vCPUs share its Stage 2
// window and differ in ctx/EL1/FP banks and vGIC redistributor). Each
// executes on its static affinity core, time-shared with the other
// VCPUs of that core by an independent per-core scheduler instance. A switch swaps the live
// TrapContext frame on the EL2 stack with the target VCPU's saved
// copy, so the common vec.S restore path resumes whichever guest is
// now current — identical from the HVC path (yield/exit) and the IRQ
// path (preemption). Guest wfi traps (HCR_EL2.TWI) park the VCPU as
// kBlocked until a deliverable vIRQ wakes it; with nothing runnable
// locally the scheduler idles at EL2 (wfi + IRQ drain) — a cross-call
// may still hand the core new work.
//
// Ownership rule: every function here that names a VCPU must run on
// that VCPU's affinity core (the smp component routes foreign
// requests). Only the machine-wide alive counter is shared.
//
// Boot: RuntimeStart seeds every VCPU; MainLoop enters the per-core
// scheduler (VCPU 0 on the primary; secondaries start idle). Entries
// past [0] stay kOff until a guest issues HVC_VM_START.

#include "core_vcpu/fp_model.hpp"
#include "core_vcpu/lifecycle_model.hpp"
#include "core_vcpu/sched_model.hpp"
#include "hal/arch/aarch64/el1_context.hpp"
#include "hal/arch/aarch64/fp.hpp"
#include "nova/abi/guest.hpp"
#include "nova/arch/trap_context.hpp"
#include "trap_handler/fp_simd.hpp"
#include "trap_handler/guest_fault.hpp"
#include "trap_handler/hvc.hpp"
#include "trap_handler/wfx.hpp"

#include <cib/top.hpp>
#include <cstddef>
#include <cstdint>
#include <flow/flow.hpp>

namespace nova {

// Per-VCPU runtime state, owned by core_vcpu. `ctx` and `el1` hold the
// guest's machine state while it is NOT resident; the resident VCPU's
// state lives in the hardware registers and the live trap frame. The
// virtual interrupt state (redistributor, pending, LR shadows) is owned
// by the vgic component, keyed by the same index.
struct Vcpu {
  using State = sched::State;

  const GuestDescriptor* guest = nullptr;
  TrapContext            ctx{}; // GP regs + SP_EL1 + ELR/SPSR_EL2
  arch::El1SysregBank    el1{}; // EL1 sysregs + SP_EL0 + CNTV
  arch::FpBank           fp{};  // Q0–Q31 + FPSR/FPCR — stale while owner (fp_model.hpp)
  State                  state = State::kOff;
};

} // namespace nova

namespace nova::vcpu {

// The VCPU currently resident on this physical core.
auto current() noexcept -> Vcpu&;
auto current_index() noexcept -> std::size_t;

// Round-robin to the next kReady VCPU; no-op when none is ready.
// `live` is the trap frame of the calling guest (return values for the
// yielding guest must be written into it BEFORE calling).
void yield_current(TrapContext* live) noexcept;

// Trapped wfi: park the calling VCPU as kBlocked and schedule away —
// or idle at EL2 when nothing is runnable. An armed, unmasked CNTV is
// mirrored into a soft-timer wake-up (parked CNTV state cannot meet
// its hardware condition while non-resident).
void block_current(TrapContext* live) noexcept;

// kBlocked → kReady (no-op for other states). Called when a
// deliverable vIRQ is posted and when the mirrored CNTV deadline
// expires; cancels the soft-timer mirror either way.
void wake(std::size_t index) noexcept;

// Cold-start a VM: seed its vcpu 0 from the descriptor and mark it
// ready. False when the VM is out of range, vcpu 0 is not kOff, or its
// affinity is not this core (HVC_VM_START is owned by smp, which
// routes foreign targets).
[[nodiscard]] auto start_vm(std::size_t vm) noexcept -> bool;

// PSCI CPU_ON: seed a secondary vCPU slot at `entry` with context_id
// in x0 (SP stays 0 — PSCI leaves it to the guest). False when the
// slot is invalid, not kOff, foreign-affinity, or its VM has retired.
[[nodiscard]] auto start_vcpu(std::size_t slot, std::uint64_t entry, std::uint64_t context_id) noexcept -> bool;

// Retire one local vCPU (PSCI CPU_OFF, VM-stop fan-out). A resident
// target schedules away through `live`; a kOff or foreign one is a
// no-op. The VM's watchdog is cancelled with its last vCPU.
void stop_vcpu(std::size_t slot, TrapContext* live) noexcept;

// Warm reset of a VM, on vcpu 0's affinity core (smp routes and has
// already fanned stop-requests to the secondaries): reload the
// pristine image and reseed vcpu 0 — resident in place, otherwise
// kReady. An all-kOff VM is ignored (start_vm is the only revival
// path); an exhausted restart budget (lifecycle_model.hpp) stops the
// VM instead.
void reset_vm(std::size_t vm, TrapContext* live) noexcept;

// HVC_EXIT epilogue: retire the calling vCPU and schedule away; halts
// the machine once every vCPU machine-wide is kOff (kBlocked ones keep
// it alive — they are owed a wake-up).
void exit_current(TrapContext* live) noexcept;

// Deliver a private vIRQ to a vCPU through the vGIC: pends the INTID
// and injects it into a free list register once the guest's enable
// bits allow it (resident targets immediately, others on switch-in).
// Wakes a kBlocked target when the result is deliverable. False when
// the target is invalid, off, or owned by another core (smp routes
// foreign posts).
[[nodiscard]] auto post_virq(std::size_t slot, std::uint32_t vintid) noexcept -> bool;

// Is this vCPU slot powered on? Safe from any core (one-byte read,
// possibly stale) — PSCI AFFINITY_INFO's answer is best-effort by
// specification.
[[nodiscard]] auto vcpu_on(std::size_t slot) noexcept -> bool;

// Seed all VCPUs from guest_table() (RuntimeStart).
void init() noexcept;

// Enter this core's scheduler: run the first local kReady VCPU (ERET
// — the only way back is a hardware trap), or idle in wfi + IRQ drain
// until a cross-call hands the core one. The primary enters VCPU 0
// immediately; secondaries start idle.
[[noreturn]] void enter_cpu() noexcept;

} // namespace nova::vcpu

namespace nova {

struct core_vcpu_component {
  // Claims HVC_YIELD (HVC_VM_START belongs to smp — affinity routing).
  static void handle_hvc(HvcCall* call) noexcept;

  // Claims every WFx trap: wfe yields, wfi blocks (unless a
  // deliverable vIRQ is already pending — then it is a NOP).
  static void handle_wfx(WfxCall* call) noexcept;

  // Claims every FP/SIMD trap: lazy bank swap — save the previous
  // owner's live state, restore the caller's, clear the trap.
  static void handle_fp_simd(FpSimdCall* call) noexcept;

  // Unrecoverable guest fault: retire the faulting VCPU, keep the rest
  // of the machine running.
  static void handle_guest_fault(GuestFaultCall* call) noexcept;

  constexpr static auto INIT  = flow::action<"core_vcpu_init">([]() noexcept { vcpu::init(); });
  constexpr static auto ENTER = flow::action<"core_vcpu_enter">([]() noexcept { vcpu::enter_cpu(); });

  constexpr static auto config = cib::config(cib::extend<cib::RuntimeStart>(*INIT), cib::extend<cib::MainLoop>(*ENTER),
                                             cib::extend<HvcService>(&core_vcpu_component::handle_hvc),
                                             cib::extend<WfxService>(&core_vcpu_component::handle_wfx),
                                             cib::extend<FpSimdService>(&core_vcpu_component::handle_fp_simd),
                                             cib::extend<GuestFaultService>(&core_vcpu_component::handle_guest_fault));
};

} // namespace nova
