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
#include "vgic/vgic_delivery.hpp"

#include <cib/top.hpp>
#include <cstddef>
#include <cstdint>
#include <flow/flow.hpp>

namespace nova::vgic {

using ReevaluateHook = void (*)(std::size_t slot) noexcept;

// Cold boot (primary): per-core bring-up plus the shared LR count and
// residency table. Discovers the implemented LR count and clears the
// list registers (their reset state is UNKNOWN).
void init() noexcept;

// Per-core half only — ICH_* and the maintenance PPI are banked per
// PE. Secondaries run this on themselves (smp bring-up).
void init_cpu() noexcept;

// Install the cross-core owner routing hook after SMP is initialized.
// The hook is boot-immutable before any guest can issue MMIO writes.
void set_reevaluate_hook(ReevaluateHook hook) noexcept;

// Reset one VCPU's virtual interrupt state to boot values (seed time).
void cpu_reset(std::size_t index) noexcept;

// Move the virtual CPU interface between hardware and the shadow bank
// around a VCPU switch. cpu_restore marks `index` resident.
void cpu_save(std::size_t index) noexcept;
void cpu_restore(std::size_t index) noexcept;

// Mark an INTID pending for a VCPU and deliver what fits into its
// list registers. A private INTID lands in the slot's redistributor;
// an SPI lands in its VM's distributor bank. Its current route is
// re-read atomically with the pending update, then that owner is
// reevaluated. False beyond the advertised INTID range.
[[nodiscard]] auto post(std::size_t index, std::uint32_t vintid) noexcept -> bool;
[[nodiscard]] auto post_tracked(std::size_t index, std::uint32_t vintid, std::uint32_t physical_intid,
                                std::uint64_t generation) noexcept -> bool;

// Refill one owner-local target after a register-state change and
// report whether it now has a deliverable interrupt.
[[nodiscard]] auto reevaluate(std::size_t index) noexcept -> bool;

// The vCPU index an SPI is routed to inside `vm` (GICD_IROUTER Aff0,
// out-of-range routes fall back to vCPU 0). Injectors combine it with
// slot_of(vm, ...) to pick the post target.
[[nodiscard]] auto spi_target_vcpu(std::size_t vm, std::uint32_t intid) noexcept -> std::size_t;

// Reset a VM's distributor bank (SPI state) to boot values — the VM
// warm-reset path pairs this with per-vCPU cpu_reset.
void vm_reset(std::size_t vm) noexcept;

// True when the VCPU has a virtual interrupt that would be signaled to
// it: software-pending and enabled, or already pending in an LR shadow.
// This is the wfi wake-up predicate — a disabled pending INTID keeps
// the VCPU asleep, matching what the hardware GIC would (not) signal.
[[nodiscard]] auto has_deliverable(std::size_t index) noexcept -> bool;

} // namespace nova::vgic

namespace nova {

struct VirtualEoiCall {
  std::size_t    slot          = 0;
  std::uint32_t  virtual_intid = 0;
  vgic::EoiToken token{};
  bool           handled = false;
};

struct VirtualEoiService : public callback::service<VirtualEoiCall*> {};

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

  constexpr static auto config = cib::config(cib::exports<VirtualEoiService>, cib::extend<cib::RuntimeStart>(*INIT),
                                             cib::extend<MmioService>(&vgic_component::handle_mmio),
                                             cib::extend<IrqService>(&vgic_component::handle_irq),
                                             cib::extend<SysregService>(&vgic_component::handle_sysreg));
};

} // namespace nova
