#pragma once

// components/core_vcpu/include/core_vcpu.hpp
//
// VCPU state, cooperative scheduler, and EL1 entry component.
//
// One Vcpu per guest_table() entry, all executing on the single
// physical core by time-sharing. Switches happen only on HVC traps
// (cooperative): the live TrapContext frame on the EL2 stack is swapped
// with the target VCPU's saved copy, so the common vec.S restore path
// resumes whichever guest is now current. Guests that wait must poll +
// HVC_YIELD — wfi is not trapped and would stall the whole core.
//
// Boot: RuntimeStart seeds every VCPU; MainLoop ERETs into VCPU 0.
// Entries past [0] stay kOff until a guest issues HVC_VM_START.

#include "components/trap_handler/include/trap_handler.hpp"
#include "nova/guest.hpp"
#include "nova/trap_context.hpp"

#include <cib/top.hpp>
#include <cstddef>
#include <cstdint>
#include <flow/flow.hpp>

namespace nova {

// EL1 system registers that carry live guest state across a switch.
// TrapContext only covers what the EL2 trap banked: guests own VBAR_EL1
// (demo vectors), and ELR/SPSR_EL1 are in flight when a guest hypercalls
// from inside its own IRQ handler. CNTV_CTL/CVAL bank the guest's
// native virtual timer (CVAL, not TVAL — the absolute deadline survives
// the time spent parked; a non-resident timer simply cannot fire).
// MMU-off flat guests have no further EL1 state; SCTLR_EL1-class
// registers join the bank when such guests get time-shared (backlog).
struct El1SysregBank {
  std::uint64_t vbar      = 0;
  std::uint64_t elr       = 0;
  std::uint64_t spsr      = 0;
  std::uint64_t sp_el0    = 0;
  std::uint64_t cntv_ctl  = 0; // reset: timer disabled
  std::uint64_t cntv_cval = 0;
};

// Per-VCPU runtime state, owned by core_vcpu. `ctx` and `el1` hold the
// guest's machine state while it is NOT resident; the resident VCPU's
// state lives in the hardware registers and the live trap frame. The
// virtual interrupt state (redistributor, pending, LR shadows) is owned
// by the vgic component, keyed by the same index.
struct Vcpu {
  enum class State : std::uint8_t {
    kOff,     // never started, or exited
    kReady,   // runnable, waiting for the scheduler
    kRunning, // resident on the core
  };

  const GuestDescriptor* guest = nullptr;
  TrapContext            ctx{}; // GP regs + SP_EL1 + ELR/SPSR_EL2
  El1SysregBank          el1{}; // VBAR/ELR/SPSR_EL1 + SP_EL0
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

// HVC_VM_START: (re)seed and mark a kOff VCPU ready. False when the
// index is out of range or the target is not kOff.
[[nodiscard]] auto start_vm(std::size_t index) noexcept -> bool;

// HVC_EXIT epilogue: retire the calling VCPU and switch to the next
// runnable one; halts the machine when none remains.
void exit_current(TrapContext* live) noexcept;

// Deliver a private vIRQ to a VCPU through the vGIC: pends the INTID
// and injects it into a free list register once the guest's enable
// bits allow it (resident targets immediately, others on switch-in).
// False when the target is invalid or off.
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

  // Unrecoverable guest fault: retire the faulting VCPU, keep the rest
  // of the machine running.
  static void handle_guest_fault(GuestFaultCall* call) noexcept;

  constexpr static auto INIT  = flow::action<"core_vcpu_init">([]() noexcept { vcpu::init(); });
  constexpr static auto ENTER = flow::action<"core_vcpu_enter">([]() noexcept { vcpu::enter_guest(); });

  constexpr static auto config = cib::config(cib::extend<cib::RuntimeStart>(*INIT), cib::extend<cib::MainLoop>(*ENTER),
                                             cib::extend<HvcService>(&core_vcpu_component::handle_hvc),
                                             cib::extend<GuestFaultService>(&core_vcpu_component::handle_guest_fault));
};

} // namespace nova
