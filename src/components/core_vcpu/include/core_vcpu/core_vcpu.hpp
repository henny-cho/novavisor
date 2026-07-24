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
// requests). Other cores see only atomic power-state and counter-offset
// snapshots; the detailed scheduler state remains owner-local.
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

// Cross-core-visible PSCI power state. kOnPending is reserved by the
// requesting core before a CPU_ON/VM_START mailbox message becomes
// visible, so concurrent callers cannot both report success.
enum class PowerState : std::uint8_t { kOff, kOnPending, kOn };

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

// Reserve an off slot for one start request. The atomic Off→OnPending
// transition is safe from any core. cancel_start rolls back a request
// rejected before its owner activates it.
[[nodiscard]] auto reserve_start(std::size_t slot) noexcept -> bool;
void               cancel_start(std::size_t slot) noexcept;

// Cold-start is split so device isolation can adopt the new generation
// before vcpu 0 becomes runnable. A zero generation means preparation
// failed; publication accepts only the exact prepared generation.
[[nodiscard]] auto prepare_start_vm(std::size_t vm) noexcept -> std::uint64_t;
[[nodiscard]] auto publish_start_vm(std::size_t vm, std::uint64_t generation) noexcept -> bool;

// Replace the seeded boot instance before MainLoop starts. A preboot
// device verifier uses this after quarantining the initial generation,
// so the guest and its streams become active under one fresh token.
[[nodiscard]] auto renew_preboot_generation(std::size_t vm) noexcept -> std::uint64_t;

// Complete a reserved PSCI CPU_ON: seed a secondary vCPU slot at
// `entry` with context_id in x0 (SP stays 0 — PSCI leaves it to the
// guest). False when the reservation is absent, the slot is invalid,
// foreign-affinity, or its VM has retired.
[[nodiscard]] auto start_vcpu(std::size_t slot, std::uint64_t entry, std::uint64_t context_id) noexcept -> bool;

// Split retirement used by the SMP reset coordinator: publish kOff and
// return whether the target was resident, allowing the caller to send
// its ACK before entering the scheduler idle loop.
[[nodiscard]] auto retire_vcpu(std::size_t slot) noexcept -> bool;
void               schedule_after_retire(TrapContext* live) noexcept;

// Temporarily keep empty schedulers draining cross-call IRQs while all
// vCPUs of a VM are quiesced for reset.
void begin_lifecycle_transition() noexcept;
void end_lifecycle_transition() noexcept;

// Warm reset follows the same prepare/publish boundary. Memory restore
// and generation advance happen while every vCPU remains off.
[[nodiscard]] auto prepare_reset_quiesced_vm(std::size_t vm) noexcept -> std::uint64_t;
[[nodiscard]] auto publish_reset_vm(std::size_t vm, std::uint64_t generation) noexcept -> bool;

// Deliver a private vIRQ to a vCPU through the vGIC: pends the INTID
// and injects it into a free list register once the guest's enable
// bits allow it (resident targets immediately, others on switch-in).
// SPIs are VM-global and stay pending even when the route snapshot's
// target is off; the vGIC re-reads the current route after publishing.
// False for invalid/foreign targets and private posts to an off vCPU.
[[nodiscard]] auto post_virq(std::size_t slot, std::uint32_t vintid) noexcept -> bool;

// Is this vCPU slot powered on? Safe from any core through an atomic
// published snapshot.
[[nodiscard]] auto vcpu_on(std::size_t slot) noexcept -> bool;

// Exact published state for PSCI CPU_ON/AFFINITY_INFO. Invalid slots
// read as kOff; callers validate guest topology separately.
[[nodiscard]] auto power_state(std::size_t slot) noexcept -> PowerState;

// Is any vCPU of this VM powered on? Safe from every core through the
// same atomic snapshots as vcpu_on().
[[nodiscard]] auto vm_on(std::size_t vm) noexcept -> bool;

// Monotonic boot-instance generation. Watchdog updates carry this
// value so delayed work from an old instance cannot alter a new one.
[[nodiscard]] auto vm_generation(std::size_t vm) noexcept -> std::uint64_t;

// Refill a local target's vGIC state after distributor or sibling
// redistributor writes. A newly deliverable interrupt wakes kBlocked.
void reevaluate_virq(std::size_t slot) noexcept;

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

  constexpr static auto INIT  = flow::action<"core_vcpu_init">([]() noexcept { vcpu::init(); });
  constexpr static auto ENTER = flow::action<"core_vcpu_enter">([]() noexcept { vcpu::enter_cpu(); });

  constexpr static auto config = cib::config(cib::extend<cib::RuntimeStart>(*INIT), cib::extend<cib::MainLoop>(*ENTER),
                                             cib::extend<HvcService>(&core_vcpu_component::handle_hvc),
                                             cib::extend<WfxService>(&core_vcpu_component::handle_wfx),
                                             cib::extend<FpSimdService>(&core_vcpu_component::handle_fp_simd));
};

} // namespace nova
