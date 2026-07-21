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
#include "nova/arch/data_abort.hpp" // esr::kSrtZeroReg
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
  std::uint64_t hcr  = gic_virt::kIchHcrBase;
};

// No VCPU owns this core's virtual CPU interface (before the core's
// first switch-in).
inline constexpr std::size_t kNoResident = ~std::size_t{0};

// Per-vCPU state (flat slot-indexed), touched only by the owning core
// (core_vcpu routes); the residency scalar is per-core — ICH_* is
// banked per PE.
std::array<CpuState, kMaxVcpus>        g_cpu;
std::array<HwBank, kMaxVcpus>          g_hw;
std::array<std::size_t, cpu::kMaxCpus> g_resident{}; // init() presets kNoResident
std::size_t                            g_lr_count = 0;

// One distributor view per VM — the SPI banks are VM-global state
// (enable/pending/route shared by the VM's vCPUs). Two cores' guests
// can MMIO their views concurrently and an SPI post can race a sibling
// vCPU's MMIO — one lock serializes every distributor and redistributor
// register-file RMW (MMIO write, post, refill).
std::array<DistState, kMaxGuests> g_dist;
sync::SpinLock                    g_dist_lock;

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

  bool overflow = false;
  {
    sync::Guard       guard{g_dist_lock}; // refill claims `pending` bits — races sibling-frame MMIO
    const std::size_t vm = vm_of(index);
    overflow =
        refill(cpu, g_lr_count, &g_dist[vm], static_cast<std::uint32_t>(vcpu_of(index)), guest_table()[vm].vcpus);
  }
  const std::uint64_t hcr = gic_virt::kIchHcrBase | (overflow ? gic_virt::kIchHcrUie : 0U);

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
  const std::size_t slot  = resident_here();
  DistState&        dist  = g_dist[vm_of(slot)];
  bool              known = false;
  {
    sync::Guard guard{g_dist_lock}; // SPI banks race post/refill across cores
    if (call->write) {
      known = dist_write(dist, off, call->size, call->value);
    } else {
      const MmioRead r = dist_read(dist, off, call->size);
      known            = r.known;
      call->value      = r.value;
    }
  }
  if (!known) {
    log_raz_wi("GICD", off);
    return;
  }
  // Enable/route/pending writes may unlock queued SPIs for this vCPU;
  // a sibling routed elsewhere picks them up on its own next flush.
  if (call->write) {
    flush(slot);
  }
}

// A GICR access selects a frame by stride; the frame is the vCPU index
// within the ACCESSING guest's VM. Frames past the VM's vcpu count are
// RAZ/WI (the guest's TYPER walk stops at Last and never gets there).
void redist_mmio(MmioCall* call, std::uint64_t off) noexcept {
  const std::size_t      frame = off / kGicrFrameSize;
  const std::size_t      vm    = vm_of(resident_here());
  const GuestDescriptor& guest = guest_table()[vm];
  if (frame >= guest.vcpus) {
    if (!call->write) {
      call->value = 0;
    }
    log_raz_wi("GICR", off);
    return;
  }
  const std::size_t slot = slot_of(vm, frame);
  const RedistId    id{.number = static_cast<std::uint32_t>(frame), .last = frame == guest.vcpus - 1U};
  CpuState&         cpu    = g_cpu[slot];
  const std::size_t in_off = off % kGicrFrameSize;

  if (call->write) {
    bool known = false;
    {
      sync::Guard guard{g_dist_lock}; // sibling frames are cross-core writable
      known = redist_write(cpu.redist, in_off, call->size, call->value);
    }
    if (!known) {
      log_raz_wi("GICR", off);
      return;
    }
    // Enable/pending changes may unlock queued vIRQs — but only the
    // owning core may touch the target's LR/HCR state. A sibling frame
    // written from another core is picked up by the owner's next flush
    // (post, maintenance, switch-in); guests program their own frame
    // on their own core, so the deferred case is theoretical.
    if (guest.cpu[frame] == cpu::id()) {
      flush(slot);
    }
    return;
  }
  MmioRead r;
  {
    sync::Guard guard{g_dist_lock}; // sibling frames are cross-core writable
    r = redist_read(cpu.redist, in_off, call->size, id);
  }
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
  {
    sync::Guard guard{g_dist_lock}; // a sibling can MMIO this redistributor frame
    g_cpu[index] = CpuState{};
  }
  g_hw[index] = HwBank{};
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
  if (vintid >= kMaxIntid) {
    return false; // beyond the advertised INTID range
  }
  {
    sync::Guard guard{g_dist_lock}; // pending RMW races sibling-frame MMIO
    if (vintid < kNumPrivate) {
      g_cpu[index].redist.pending |= 1U << vintid;
    } else {
      g_dist[vm_of(index)].spi_pending |= 1U << (vintid - kNumPrivate);
    }
  }
  flush(index);
  return true;
}

auto spi_target_vcpu(std::size_t vm, std::uint32_t intid) noexcept -> std::size_t {
  sync::Guard guard{g_dist_lock};
  return spi_target(g_dist[vm], intid, guest_table()[vm].vcpus);
}

void vm_reset(std::size_t vm) noexcept {
  sync::Guard guard{g_dist_lock};
  g_dist[vm] = DistState{};
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
  const std::size_t vm = vm_of(index);
  {
    sync::Guard guard{g_dist_lock};
    if (deliverable(cpu.redist) != 0U ||
        spi_deliverable(g_dist[vm], static_cast<std::uint32_t>(vcpu_of(index)), guest_table()[vm].vcpus) != 0U) {
      return true;
    }
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
  if (call->ipa >= gic_virt::kGicrIpaBase &&
      call->ipa < gic_virt::kGicrIpaBase + kMaxVcpusPerVm * vgic::kGicrFrameSize) {
    call->handled = true;
    vgic::redist_mmio(call, call->ipa - gic_virt::kGicrIpaBase);
  }
}

// ICH_HCR.TC (set for vSGI routing) traps every ICC register common to
// Group 0 and Group 1, not just the SGI generators. The resident guest
// took the trap on its own core, so its VMCR is live in hardware.
void vgic_component::handle_sysreg(SysregCall* call) noexcept {
  const esr::SysregTrap& s = call->sysreg;
  if (s.op0 != 3 || s.op1 != 0) {
    return;
  }

  // ICC_PMR_EL1 (S3_0_C4_C6_0): the priority mask lives in
  // ICH_VMCR_EL2.VPMR [31:24] — emulate the ICV view the trap bypassed.
  if (s.crn == 4 && s.crm == 6 && s.op2 == 0) {
    call->handled                     = true;
    constexpr std::uint64_t kVpmrMask = 0xFFULL << 24U;
    const std::uint64_t     vmcr      = gic_virt::read_vmcr();
    if (s.write) {
      const std::uint64_t pmr = (s.rt == esr::kSrtZeroReg ? 0 : call->ctx->x[s.rt]) & 0xFFU;
      gic_virt::write_vmcr((vmcr & ~kVpmrMask) | (pmr << 24U));
    } else if (s.rt != esr::kSrtZeroReg) {
      call->ctx->x[s.rt] = (vmcr >> 24U) & 0xFFU;
    }
    return;
  }

  if (s.crn != 12 || s.crm != 11) {
    if (s.crn == 12 && s.crm == 12 && s.op2 == 4) { // ICC_CTLR_EL1: EOImode 0, nothing writable we honor
      call->handled = true;
      if (!s.write && s.rt != esr::kSrtZeroReg) {
        call->ctx->x[s.rt] = 0;
      }
    }
    return;
  }
  switch (s.op2) {
  case 1: // ICC_DIR_EL1 — deactivation is a NOP with EOImode 0
  case 6: // ICC_ASGI1R_EL1 — no other security state
  case 7: // ICC_SGI0R_EL1 — no Group 0 SGIs
    call->handled = true;
    return;
  case 3: // ICC_RPR_EL1 — idle priority (no active interrupt tracked)
    call->handled = true;
    if (!s.write && s.rt != esr::kSrtZeroReg) {
      call->ctx->x[s.rt] = 0xFF;
    }
    return;
  default:
    return; // op2 5 (ICC_SGI1R) is claimed by smp
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
