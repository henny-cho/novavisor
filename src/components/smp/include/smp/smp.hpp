#pragma once

// components/smp/include/smp/smp.hpp
//
// Physical secondary-core bring-up and the cross-core call path.
//
// Bring-up (RuntimeStart, primary, ordered by the project nexus after
// every other init action — secondaries must observe fully initialized
// shared state): powers each secondary on through the PSCI SMC conduit,
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

#include "core_gic/core_gic.hpp"
#include "core_vcpu/core_vcpu.hpp"
#include "smmu/smmu.hpp"
#include "trap_handler/guest_fault.hpp"
#include "trap_handler/hvc.hpp"
#include "trap_handler/sysreg.hpp"
#include "vgic/vgic.hpp"

#include <cib/top.hpp>
#include <cstddef>
#include <cstdint>
#include <flow/flow.hpp>

namespace nova::smp {

using VmOwnerCall = void (*)(std::size_t vm, std::uint64_t a, std::uint64_t b, std::uint64_t c) noexcept;

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
// Returns a PSCI status code (nova/abi/psci.h) so the emulated
// CPU_ON can hand it straight back to the guest.
[[nodiscard]] auto cpu_on(std::size_t slot, std::uint64_t entry, std::uint64_t context_id) noexcept -> std::int32_t;

// Execute idempotent VM-level work on the boot vCPU's affinity core.
// Remote calls are accepted into the cross-call mailbox.
[[nodiscard]] auto invoke_vm_owner(std::size_t vm, VmOwnerCall fn, std::uint64_t a, std::uint64_t b,
                                   std::uint64_t c) noexcept -> bool;

// Coalesced owner-local vGIC refill/wake request. It uses a per-core
// dirty bitset rather than mailbox capacity because reevaluation is
// idempotent and state-derived.
void reevaluate_virq(std::size_t slot) noexcept;

// VM-wide power operations route through the boot owner and share one
// serialized quiesce protocol. DMA is drained and detached before a
// reset restores memory; the new generation is attached before vcpu 0
// becomes runnable.
void               stop_vm(std::size_t vm, TrapContext* live) noexcept;
void               cpu_off(std::size_t slot, TrapContext* live) noexcept;
[[nodiscard]] auto reset_vm(std::size_t vm, TrapContext* live, bool from_irq = false) noexcept -> bool;

} // namespace nova::smp

namespace nova {

struct smp_component {
  // Claims HVC_VM_START (affinity-routed).
  static void handle_hvc(HvcCall* call) noexcept;

  // Converts an unrecoverable guest exception into a bounded VM-wide
  // warm reset while unrelated VMs keep running.
  static void handle_guest_fault(GuestFaultCall* call) noexcept;

  // Routes an isolated DMA fault to the VM owner. A generation check
  // prevents delayed notices from resetting a newer VM instance.
  static void handle_dma_fault(DmaFaultCall* call) noexcept;

  // Claims the cross-call SGI: executes queued foreign requests.
  static void handle_irq(IrqCall* call) noexcept;

  // Claims trapped ICC_SGI1R_EL1 writes (ICH_HCR.TC): decodes the
  // guest's SGI targets and posts the vINTID to each sibling vCPU,
  // affinity-routed — the guest's own IPIs cross physical cores here.
  static void handle_sysreg(SysregCall* call) noexcept;

  // Routes a vgic reevaluation request to the slot's owning core. Wired
  // at compile time, so it is live before the first guest runs — no
  // window in which a posted vIRQ wake could be dropped.
  static void handle_virq_reevaluate(VirqReevaluateCall* call) noexcept;

  constexpr static auto INIT = flow::action<"smp_start_secondaries">([]() noexcept { smp::start_secondaries(); });

  // A secondary begins touching shared state (GIC frames, VM table,
  // timer queues) the moment CPU_ON lands, so this action must run
  // last in RuntimeStart. That constraint is expressed by the project
  // nexus, which owns the whole boot chain.
  constexpr static auto config = cib::config(
      cib::extend<cib::RuntimeStart>(*INIT), cib::extend<HvcService>(&smp_component::handle_hvc),
      cib::extend<GuestFaultService>(&smp_component::handle_guest_fault),
      cib::extend<DmaFaultService>(&smp_component::handle_dma_fault),
      cib::extend<IrqService>(&smp_component::handle_irq), cib::extend<SysregService>(&smp_component::handle_sysreg),
      cib::extend<VirqReevaluateService>(&smp_component::handle_virq_reevaluate));
};

} // namespace nova
