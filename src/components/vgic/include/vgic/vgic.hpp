#pragma once

// components/vgic/include/vgic/vgic.hpp
//
// Virtual GICv3 component — glue between the pure model (vgic_model.hpp + vgic_delivery.hpp)
// and the hardware virtual CPU interface:
//
//   - Emulates the GICD/GICR frames through MmioService (the frames'
//     IPAs are left unmapped in Stage 2). Unknown offsets complete
//     RAZ/WI with a log line so uncovered guest accesses stay visible.
//   - Owns the full per-VCPU virtual interrupt state: redistributor
//     registers, software pending bitmap, ICH_LR shadows and the banked
//     ICH_VMCR/ICH_HCR (guests mutate VMCR through their ICV_* view).
//   - Multiplexes pending vIRQs onto all implemented list registers;
//     overflow arms the underflow maintenance IRQ (PPI 25) and refills
//     as the guest drains its LRs.
//
// core_vcpu drives residency (cpu_save/cpu_restore on switches,
// cpu_reset on seed) and funnels vcpu::post_virq into post().

#include "core_gic/core_gic.hpp"
#include "trap_handler/mmio.hpp"
#include "trap_handler/sysreg.hpp"

#include <cib/top.hpp>
#include <cstddef>
#include <cstdint>
#include <flow/flow.hpp>

namespace nova::vgic {

// Cold boot (primary): per-core bring-up plus the shared LR count and
// residency table. Discovers the implemented LR count and clears the
// list registers (their reset state is UNKNOWN).
void init() noexcept;

// Per-core half only — ICH_* and the maintenance PPI are banked per
// PE. Secondaries run this on themselves (smp bring-up).
void init_cpu() noexcept;

// Reset one VCPU's virtual interrupt state to boot values (seed time).
void cpu_reset(std::size_t index) noexcept;

// Move the virtual CPU interface between hardware and the shadow bank
// around a VCPU switch. cpu_restore marks `index` resident.
void cpu_save(std::size_t index) noexcept;
void cpu_restore(std::size_t index) noexcept;

// Mark a private INTID pending for a VCPU and deliver what fits into
// its list registers. False for a non-private INTID.
[[nodiscard]] auto post(std::size_t index, std::uint32_t vintid) noexcept -> bool;

// True when the VCPU has a virtual interrupt that would be signaled to
// it: software-pending and enabled, or already pending in an LR shadow.
// This is the wfi wake-up predicate — a disabled pending INTID keeps
// the VCPU asleep, matching what the hardware GIC would (not) signal.
[[nodiscard]] auto has_deliverable(std::size_t index) noexcept -> bool;

} // namespace nova::vgic

namespace nova {

struct vgic_component {
  // Claims the GICD/GICR frame IPAs.
  static void handle_mmio(MmioCall* call) noexcept;

  // Claims the maintenance PPI: refills the resident VCPU's LRs.
  static void handle_irq(IrqCall* call) noexcept;

  // Claims the trapped ICC "common" registers (ICH_HCR.TC catches
  // more than the SGI generators): PMR is virtualized through the
  // live ICH_VMCR.VPMR; CTLR/RPR read fixed idle values; DIR and the
  // Group 0 generators are WI. ICC_SGI1R itself belongs to smp.
  static void handle_sysreg(SysregCall* call) noexcept;

  constexpr static auto INIT = flow::action<"vgic_init">([]() noexcept { vgic::init(); });

  constexpr static auto config = cib::config(
      cib::extend<cib::RuntimeStart>(*INIT), cib::extend<MmioService>(&vgic_component::handle_mmio),
      cib::extend<IrqService>(&vgic_component::handle_irq), cib::extend<SysregService>(&vgic_component::handle_sysreg));
};

} // namespace nova
