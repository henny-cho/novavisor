#pragma once

// components/smp/include/smp/smp.hpp
//
// Physical secondary-core bring-up and the cross-core call path.
//
// Bring-up (RuntimeStart, primary, ordered after every other init
// action — secondaries must observe fully initialized shared state):
// powers each secondary on through the firmware PSCI SMC conduit,
// targeting nova_secondary_entry (boot.S) with the core index as
// context id. The secondary initializes its banked hardware (GICR,
// ICC/ICH, timers) on its own stack and enters its scheduler idle.
// The conduit is SMC — firmware-facing — and entirely separate from
// the HVC PSCI the psci component emulates for guests.
//
// Cross-call (the ownership rule's escape hatch): a VCPU's state is
// touched only on its affinity core, so operations naming a foreign
// VM — HVC_VM_START, IVC doorbells — are enqueued into the owning
// core's mailbox and announced with a physical SGI; the receiver
// executes them locally in its IRQ drain. This component therefore
// owns HVC_VM_START (core_vcpu keeps the local start_vm/post_virq).

#include "boot_msg/boot_msg.hpp"
#include "core_gic/core_gic.hpp"
#include "core_mmu/core_mmu.hpp"
#include "core_timer/core_timer.hpp"
#include "core_vcpu/core_vcpu.hpp"
#include "soft_timer/soft_timer.hpp"
#include "trap_handler/hvc.hpp"
#include "trap_handler/sysreg.hpp"
#include "vgic/vgic.hpp"

#include <cib/top.hpp>
#include <cstddef>
#include <cstdint>
#include <flow/flow.hpp>

namespace nova::smp {

enum class CpuOnResult : std::uint8_t {
  kSuccess,
  kInvalid,
  kDenied,
  kAlreadyOn,
  kOnPending,
  kInternalFailure,
};

// Power on every secondary core and wait (bounded) for each to report
// online. A core that fails to start is logged and skipped — the
// system continues on the cores it has.
void start_secondaries() noexcept;

// Affinity-routed variants of the core_vcpu operations: executed
// directly when the target belongs to this core, delegated through
// the owning core's mailbox otherwise. A delegated result means
// "accepted", not "completed" — the owner may still reject it.
// start_vm/reset_vm take a VM; the rest take a vCPU slot.
[[nodiscard]] auto start_vm(std::size_t vm) noexcept -> bool;
[[nodiscard]] auto post_virq(std::size_t slot, std::uint32_t vintid) noexcept -> bool;
[[nodiscard]] auto cpu_on(std::size_t slot, std::uint64_t entry, std::uint64_t context_id) noexcept -> CpuOnResult;

// VM-wide power operations, fanned out per vCPU. stop_vm retires every
// live vCPU (the caller's own last — that one schedules away through
// `live` and the call does not return to guest code). reset_vm routes
// through the boot owner, waits for current-epoch quiesce ACKs, then
// restores memory and reseeds vcpu 0.
void stop_vm(std::size_t vm, TrapContext* live) noexcept;
void reset_vm(std::size_t vm, TrapContext* live, bool from_irq = false) noexcept;

} // namespace nova::smp

namespace nova {

struct smp_component {
  // Claims HVC_VM_START (affinity-routed).
  static void handle_hvc(HvcCall* call) noexcept;

  // Claims the cross-call SGI: executes queued foreign requests.
  static void handle_irq(IrqCall* call) noexcept;

  // Claims trapped ICC_SGI1R_EL1 writes (ICH_HCR.TC): decodes the
  // guest's SGI targets and posts the vINTID to each sibling vCPU,
  // affinity-routed — the guest's own IPIs cross physical cores here.
  static void handle_sysreg(SysregCall* call) noexcept;

  constexpr static auto INIT = flow::action<"smp_start_secondaries">([]() noexcept { smp::start_secondaries(); });

  // Explicit flow edges: a secondary begins touching shared state
  // (GIC frames, VM table, timer queues) the moment CPU_ON lands, so
  // every other RuntimeStart action must have completed first. The
  // chain also pins the boot order the other inits relied on
  // implicitly (topo_sort gives no order without edges).
  constexpr static auto config = cib::config(
      cib::extend<cib::RuntimeStart>(core_mmu_component::INIT >> core_gic_component::INIT >> vgic_component::INIT >>
                                     core_timer_component::INIT >> soft_timer_component::INIT >>
                                     core_vcpu_component::INIT >> boot_msg_component::PRINT_BOOT_MSG >> *INIT),
      cib::extend<HvcService>(&smp_component::handle_hvc), cib::extend<IrqService>(&smp_component::handle_irq),
      cib::extend<SysregService>(&smp_component::handle_sysreg));
};

} // namespace nova
