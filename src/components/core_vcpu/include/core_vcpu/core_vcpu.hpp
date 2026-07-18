#pragma once

// components/core_vcpu/include/core_vcpu/core_vcpu.hpp
//
// VCPU state, scheduler, and EL1 entry component.
//
// One Vcpu per guest_table() entry, all executing on the single
// physical core by time-sharing. A switch swaps the live TrapContext
// frame on the EL2 stack with the target VCPU's saved copy, so the
// common vec.S restore path resumes whichever guest is now current —
// identical from the HVC path (yield/exit) and the IRQ path
// (preemption). Guest wfi traps (HCR_EL2.TWI) park the VCPU as
// kBlocked until a deliverable vIRQ wakes it; with nothing runnable
// the scheduler idles at EL2 (wfi + IRQ drain).
//
// Boot: RuntimeStart seeds every VCPU; MainLoop ERETs into VCPU 0.
// Entries past [0] stay kOff until a guest issues HVC_VM_START.

#include "core_vcpu/fp_model.hpp"
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

// HVC_VM_START: (re)seed and mark a kOff VCPU ready. False when the
// index is out of range or the target is not kOff.
[[nodiscard]] auto start_vm(std::size_t index) noexcept -> bool;

// HVC_EXIT epilogue: retire the calling VCPU and schedule away; halts
// the machine once every VCPU is kOff (kBlocked ones keep it alive —
// they are owed a wake-up).
void exit_current(TrapContext* live) noexcept;

// Deliver a private vIRQ to a VCPU through the vGIC: pends the INTID
// and injects it into a free list register once the guest's enable
// bits allow it (resident targets immediately, others on switch-in).
// Wakes a kBlocked target when the result is deliverable. False when
// the target is invalid or off.
[[nodiscard]] auto post_virq(std::size_t index, std::uint32_t vintid) noexcept -> bool;

// Seed all VCPUs from guest_table() (RuntimeStart).
void init() noexcept;

// Transfer to EL1 via ERET into VCPU 0. [[noreturn]] — the only way
// back is a hardware trap.
[[noreturn]] void enter_guest() noexcept;

} // namespace nova::vcpu

namespace nova {

struct core_vcpu_component {
  // Claims HVC_YIELD and HVC_VM_START.
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
  constexpr static auto ENTER = flow::action<"core_vcpu_enter">([]() noexcept { vcpu::enter_guest(); });

  constexpr static auto config = cib::config(cib::extend<cib::RuntimeStart>(*INIT), cib::extend<cib::MainLoop>(*ENTER),
                                             cib::extend<HvcService>(&core_vcpu_component::handle_hvc),
                                             cib::extend<WfxService>(&core_vcpu_component::handle_wfx),
                                             cib::extend<FpSimdService>(&core_vcpu_component::handle_fp_simd),
                                             cib::extend<GuestFaultService>(&core_vcpu_component::handle_guest_fault));
};

} // namespace nova
