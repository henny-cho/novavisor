// components/vgic/src/vgic.cpp
//
// vGICv3 component implementation. All register semantics live in the
// pure model (vgic_model.hpp + vgic_delivery.hpp); this file only routes MMIO traps,
// maintains residency, and mirrors model state to the hardware virtual
// CPU interface.

#include "vgic/vgic.hpp"

#include "hal/console.hpp"
#include "hal/cpu.hpp"
#include "hal/gic.hpp"
#include "hal/gic_virt.hpp"
#include "nova/abi/guest.hpp"
#include "nova/sync.hpp"
#include "vgic/vgic_delivery.hpp"

#include <array>
#include <cstddef>
#include <cstdint>

namespace nova::vgic {
namespace {

// Hardware registers that carry live guest state while resident.
struct HwBank {
  std::uint64_t vmcr = gic_virt::kVmcrReset;
  std::uint64_t hcr  = gic_virt::kIchHcrEn;
};

// No VCPU owns this core's virtual CPU interface (before the core's
// first switch-in).
inline constexpr std::size_t kNoResident = ~std::size_t{0};

// Per-vCPU state, touched only by the owning core (core_vcpu routes);
// the residency scalar is per-core — ICH_* is banked per PE.
std::array<CpuState, kMaxGuests>       g_cpu;
std::array<HwBank, kMaxGuests>         g_hw;
std::array<std::size_t, cpu::kMaxCpus> g_resident{}; // init() presets kNoResident
std::size_t                            g_lr_count = 0;

// One distributor view shared by all VMs. Its state still gates
// nothing (per-INTID enables live in the per-VCPU redistributor), but
// two cores' guests can MMIO it concurrently — serialize the RMW.
DistState      g_dist;
sync::SpinLock g_dist_lock;

[[nodiscard]] auto resident_here() noexcept -> std::size_t& {
  return g_resident[cpu::id()];
}

// Push deliverable pending INTIDs of one VCPU into its list registers.
// For the resident VCPU the hardware LRs are the live truth: sync them
// into the shadow first (the guest retires entries as it runs), refill,
// and write everything back. Overflow arms the underflow maintenance
// IRQ so draining LRs pull the queue.
void flush(std::size_t index) noexcept {
  CpuState&  cpu      = g_cpu[index];
  const bool resident = index == resident_here();

  if (resident) {
    for (std::size_t i = 0; i < g_lr_count; ++i) {
      cpu.lr[i] = gic_virt::read_lr(i);
    }
  }

  const bool          overflow = refill(cpu, g_lr_count);
  const std::uint64_t hcr      = gic_virt::kIchHcrEn | (overflow ? gic_virt::kIchHcrUie : 0U);

  if (resident) {
    for (std::size_t i = 0; i < g_lr_count; ++i) {
      gic_virt::write_lr(i, cpu.lr[i]);
    }
    gic_virt::write_hcr(hcr);
  } else {
    g_hw[index].hcr = hcr;
  }
}

void log_raz_wi(const char* frame, std::uint64_t off) noexcept {
  console::write("[vgic] RAZ/WI ");
  console::write(frame);
  console::write(" offset 0x");
  console::write_hex64(off);
  console::write("\n");
}

void dist_mmio(MmioCall* call, std::uint64_t off) noexcept {
  sync::Guard guard{g_dist_lock}; // GICD is one shared view — RMW across cores
  if (call->write) {
    if (!dist_write(g_dist, off, call->size, call->value)) {
      log_raz_wi("GICD", off);
    }
    return;
  }
  const MmioRead r = dist_read(g_dist, off, call->size);
  if (!r.known) {
    log_raz_wi("GICD", off);
  }
  call->value = r.value;
}

void redist_mmio(MmioCall* call, std::uint64_t off) noexcept {
  CpuState& cpu = g_cpu[resident_here()];
  if (call->write) {
    if (redist_write(cpu.redist, off, call->size, call->value)) {
      flush(resident_here()); // enable/pending changes may unlock queued vIRQs
    } else {
      log_raz_wi("GICR", off);
    }
    return;
  }
  const MmioRead r = redist_read(cpu.redist, off, call->size);
  if (!r.known) {
    log_raz_wi("GICR", off);
  }
  call->value = r.value;
}

} // namespace

void init_cpu() noexcept {
  gic_virt::init(); // VMCR reset + HCR.En — ICH_* is banked per core
  for (std::size_t i = 0; i < gic_virt::lr_count(); ++i) {
    gic_virt::write_lr(i, 0); // reset state is UNKNOWN
  }
  gic::enable_ppi(gic_virt::kMaintenanceIntid);
}

void init() noexcept {
  init_cpu();
  g_lr_count = gic_virt::lr_count(); // boot-immutable (homogeneous cores)
  for (std::size_t c = 0; c < cpu::kMaxCpus; ++c) {
    g_resident[c] = kNoResident;
  }

  console::write("vGIC: ");
  console::write_dec64(g_lr_count);
  console::write(" list registers, GICD/GICR emulation active\n");
}

void cpu_reset(std::size_t index) noexcept {
  g_cpu[index] = CpuState{};
  g_hw[index]  = HwBank{};
}

void cpu_save(std::size_t index) noexcept {
  CpuState& cpu = g_cpu[index];
  for (std::size_t i = 0; i < g_lr_count; ++i) {
    cpu.lr[i] = gic_virt::read_lr(i);
  }
  g_hw[index].vmcr = gic_virt::read_vmcr();
  g_hw[index].hcr  = gic_virt::read_hcr();
}

void cpu_restore(std::size_t index) noexcept {
  const CpuState& cpu = g_cpu[index];
  for (std::size_t i = 0; i < g_lr_count; ++i) {
    gic_virt::write_lr(i, cpu.lr[i]);
  }
  gic_virt::write_vmcr(g_hw[index].vmcr);
  gic_virt::write_hcr(g_hw[index].hcr);
  resident_here() = index;
}

auto post(std::size_t index, std::uint32_t vintid) noexcept -> bool {
  if (vintid >= kNumPrivate) {
    return false; // SPIs are not modeled (GICD_TYPER advertises none)
  }
  g_cpu[index].redist.pending |= 1U << vintid;
  flush(index);
  return true;
}

auto has_deliverable(std::size_t index) noexcept -> bool {
  CpuState& cpu = g_cpu[index];
  if (index == resident_here()) {
    // The live LRs are the truth for the resident VCPU (the guest
    // retires entries as it runs) — refresh the shadow before judging.
    // A pending LR here is real: HCR_EL2.TWI traps every wfi, even one
    // that would complete immediately because of a pending wake event.
    for (std::size_t i = 0; i < g_lr_count; ++i) {
      cpu.lr[i] = gic_virt::read_lr(i);
    }
  }
  if (deliverable(cpu.redist) != 0U) {
    return true;
  }
  for (std::size_t i = 0; i < g_lr_count; ++i) {
    if ((cpu.lr[i] & kLrStatePending) != 0U) {
      return true;
    }
  }
  return false;
}

} // namespace nova::vgic

namespace nova {

void vgic_component::handle_mmio(MmioCall* call) noexcept {
  if (call->ipa >= gic_virt::kGicdIpaBase && call->ipa < gic_virt::kGicdIpaBase + vgic::kGicdFrameSize) {
    call->handled = true;
    vgic::dist_mmio(call, call->ipa - gic_virt::kGicdIpaBase);
    return;
  }
  if (call->ipa >= gic_virt::kGicrIpaBase && call->ipa < gic_virt::kGicrIpaBase + vgic::kGicrFrameSize) {
    call->handled = true;
    vgic::redist_mmio(call, call->ipa - gic_virt::kGicrIpaBase);
  }
}

void vgic_component::handle_irq(IrqCall* call) noexcept {
  if (call->intid != gic_virt::kMaintenanceIntid) {
    return;
  }
  call->handled = true;
  // Underflow: the guest resident on the receiving core drained its
  // LRs while software pending remained — top them up. flush() drops
  // UIE once the queue is empty, deasserting this (level) interrupt.
  vgic::flush(vgic::resident_here());
}

} // namespace nova
