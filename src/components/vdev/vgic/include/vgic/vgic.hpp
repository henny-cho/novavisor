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
// cpu_reset on seed) and funnels vcpu::post_virq into
// post_private()/post_spi().

#include "core_gic/core_gic.hpp"
#include "telemetry/telemetry.hpp"
#include "trap_handler/mmio.hpp"
#include "trap_handler/sysreg.hpp"
#include "vgic/vgic_delivery.hpp"

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

// Clear this core's residency shadow — cpu_restore's counterpart,
// called when a retiring vCPU was the one it had marked resident here.
void vacate() noexcept;

// Mark a private INTID (SGI/PPI) pending in `index`'s redistributor
// and deliver what fits into its list registers. Runs on the owning
// core (core_vcpu routes). False beyond the private range.
[[nodiscard]] auto post_private(std::size_t index, std::uint32_t vintid) noexcept -> bool;

// Mark an SPI pending in `vm`'s distributor bank. The current route
// (GICD_IROUTER Aff0) is read atomically with the pending update, then
// that owner is notified through the reevaluate fan-out — callable
// from any core; injectors need no route pre-lookup. False outside the
// advertised SPI range.
[[nodiscard]] auto post_spi(std::size_t vm, std::uint32_t vintid) noexcept -> bool;

// The virtual INTIDs post_spi accepts, inclusive. The model advertises
// this range in GICD_TYPER, so it is what a guest driver sizes itself
// to and what an injector may offer.
struct SpiBand {
  std::uint32_t lo = 0;
  std::uint32_t hi = 0;
};

[[nodiscard]] auto spi_band() noexcept -> SpiBand;

// post_spi for a hardware-backed SPI: binds an EoI token (physical
// INTID + device generation) so the guest's EOI can be forwarded to
// the right device incarnation. A live token makes a re-post
// idempotent for the same source and rejects a conflicting one.
[[nodiscard]] auto post_spi_tracked(std::size_t vm, std::uint32_t vintid, std::uint32_t physical_intid,
                                    std::uint64_t generation) noexcept -> bool;

// Refill one owner-local target after a register-state change and
// report whether it now has a deliverable interrupt.
[[nodiscard]] auto reevaluate(std::size_t index) noexcept -> bool;

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

// A vCPU's deliverable set may have changed and its LRs need refilling.
// Wired at compile time rather than through a runtime hook, so a posted
// vIRQ can never be dropped in a pre-installation window. With no
// subscriber (the minimal profile) the dispatch is a no-op — the vCPU
// refills on its next switch-in.
struct VirqReevaluateCall {
  std::size_t slot = 0;
};

struct VirqReevaluateService : public callback::service<VirqReevaluateCall*> {};

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

  // What this component offers the S layer.
  static void telemetry(TelemetryCall* call) noexcept;

  constexpr static auto INIT = flow::action<"vgic_init">([]() noexcept { vgic::init(); });

  constexpr static auto config = cib::config(
      cib::exports<VirtualEoiService, VirqReevaluateService>, cib::extend<cib::RuntimeStart>(*INIT),
      cib::extend<MmioService>(&vgic_component::handle_mmio), cib::extend<IrqService>(&vgic_component::handle_irq),
      cib::extend<SysregService>(&vgic_component::handle_sysreg),
      cib::extend<TelemetryService>(&vgic_component::telemetry));
};

} // namespace nova
