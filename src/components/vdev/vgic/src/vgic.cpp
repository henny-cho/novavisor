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
#include "nova/abi/guest_layout.h"
#include "nova/arch/esr.hpp"
#include "nova/arch/gicv3/vtr.hpp"
#include "nova/sync.hpp"
#include "trace/trace.hpp"
#include "vgic/vgic_delivery.hpp"

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>

namespace nova::vgic {
namespace {

// Hardware registers that carry live guest state while resident. The
// VMCR reset value is runtime-derived from ICH_VTR (binary-point
// minimums); init() seeds every bank and cpu_reset() re-seeds one.
struct HwBank {
  std::uint64_t vmcr = 0;
  std::uint64_t hcr  = gic_virt::kIchHcrBase;
};

// No VCPU owns this core's virtual CPU interface (before the core's
// first switch-in).
inline constexpr std::size_t kNoResident = ~std::size_t{0};

// Emulated GIC frames. The guest-platform contract fixes these IPAs
// (independent of where the board's physical GIC sits); Stage 2 leaves
// them unmapped on purpose so accesses trap into handle_mmio.
inline constexpr std::uint64_t kGicdIpaBase = NOVA_GICD_IPA_BASE;
inline constexpr std::uint64_t kGicrIpaBase = NOVA_GICR_IPA_BASE;

// Per-vCPU state (flat slot-indexed), touched only by the owning core
// (core_vcpu routes); the residency scalar is per-core — ICH_* is
// banked per PE.
std::array<CpuState, kMaxVcpus>        g_cpu;
std::array<HwBank, kMaxVcpus>          g_hw;
std::array<std::size_t, cpu::kMaxCpus> g_resident{}; // init() presets kNoResident
std::size_t                            g_lr_count   = 0;
std::uint64_t                          g_vtr        = 0; // cached ICH_VTR_EL2 (boot-immutable)
std::uint64_t                          g_vmcr_reset = 0; // vmcr_reset(g_vtr)

// One distributor view per VM — the SPI banks are VM-global state
// (enable/pending/route shared by the VM's vCPUs). Two cores' guests
// can MMIO their views concurrently and an SPI post can race a sibling
// vCPU's MMIO — a per-VM lock serializes that VM's distributor and
// redistributor register-file RMWs (MMIO write, post, refill). No path
// touches two VMs' banks, so one VM's MMIO burst never stalls another.
std::array<DistState, kMaxGuests>                      g_dist;
std::array<std::array<EoiToken, kNumSpis>, kMaxGuests> g_spi_tokens;
std::array<sync::SpinLock, kMaxGuests>                 g_vm_lock;

[[nodiscard]] auto resident_here() noexcept -> std::size_t& {
  return g_resident[cpu::id()];
}

// Announce that `slot`'s deliverable set changed. Subscribers route the
// refill to the slot's owning core; with none composed this is a no-op.
void request_reevaluate(std::size_t slot) noexcept {
  VirqReevaluateCall call{.slot = slot};
  cib::service<VirqReevaluateService>(&call);
}

// Refresh a resident vCPU's LR shadow from the live hardware registers
// — the guest retires entries as it runs, so the hardware is the truth
// while resident. Mutates the shadow; a no-op for non-resident vCPUs.
void sync_resident_lrs(std::size_t index) noexcept {
  if (index != resident_here()) {
    return;
  }
  CpuState& cpu = g_cpu[index];
  for (std::size_t i = 0; i < g_lr_count; ++i) {
    cpu.lr[i] = gic_virt::read_lr(i);
  }
}

// Push deliverable pending INTIDs of one VCPU into its list registers.
// For the resident VCPU the hardware LRs are the live truth: sync them
// into the shadow first, refill, and write everything back. Overflow
// arms the underflow maintenance IRQ so draining LRs pull the queue.
void flush(std::size_t index) noexcept {
  CpuState&  cpu      = g_cpu[index];
  const bool resident = index == resident_here();

  sync_resident_lrs(index);

  // The delivery model stays pure, so the injection is observed here
  // instead: the shadow this function already reads is diffed across
  // the refill, and a slot that newly holds an interrupt is the hop.
  std::array<std::uint64_t, kMaxLrs> before{};
  for (std::size_t i = 0; i < g_lr_count; ++i) {
    before[i] = cpu.lr[i];
  }

  bool overflow = false;
  {
    const std::size_t vm = vm_of(index);
    sync::Guard       guard{g_vm_lock[vm]}; // refill claims `pending` bits — races sibling-frame MMIO
    overflow = refill(cpu, g_lr_count, &g_dist[vm], static_cast<std::uint32_t>(vcpu_of(index)), guest_table()[vm].vcpus,
                      &g_spi_tokens[vm]);
  }
  for (std::size_t i = 0; i < g_lr_count; ++i) {
    if (cpu.lr[i] != before[i] && (cpu.lr[i] & kLrStatePending) != 0U) {
      // The generation says whether a physical interrupt is behind this
      // one: refill moves the token here, so its presence is the answer.
      trace_emit(NOVA_TRACE_EV_VGIC_INJECT, static_cast<std::uint32_t>(index),
                 lr_vintid(cpu.lr[i]) | (static_cast<std::uint64_t>(i) << 32U), cpu.lr_token[i].generation);
    }
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

void drain_eois(std::size_t index) noexcept {
  if (index != resident_here()) {
    return;
  }
  const std::uint64_t eisr = gic_virt::read_eisr();
  if (eisr == 0U) {
    return;
  }

  // The hardware is the truth for the EoI'd slots: pull them into the
  // shadow before the model consumes their tokens, then mirror the
  // slots it emptied back out.
  CpuState& cpu = g_cpu[index];
  for (std::size_t i = 0; i < g_lr_count && i < cpu.lr.size(); ++i) {
    if ((eisr & (1ULL << i)) != 0U) {
      cpu.lr[i] = gic_virt::read_lr(i);
    }
  }
  const EoiHarvest harvest = harvest_eois(cpu, eisr, g_lr_count);
  for (std::size_t i = 0; i < g_lr_count && i < cpu.lr.size(); ++i) {
    if ((harvest.cleared & (1ULL << i)) != 0U) {
      gic_virt::write_lr(i, 0);
    }
  }
  for (std::size_t i = 0; i < harvest.count; ++i) {
    trace_emit(NOVA_TRACE_EV_VGIC_EOI, static_cast<std::uint32_t>(index),
               harvest.tokens[i].virtual_intid | (static_cast<std::uint64_t>(harvest.tokens[i].physical_intid) << 32U),
               harvest.tokens[i].generation);
    VirtualEoiCall call{
        .slot          = index,
        .virtual_intid = harvest.tokens[i].virtual_intid,
        .token         = harvest.tokens[i],
    };
    cib::service<VirtualEoiService>(&call);
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
  const std::size_t slot = resident_here();
  if (slot == kNoResident) { // unreachable: a guest cannot trap before its first switch-in
    if (!call->write) {
      call->value = 0;
    }
    log_raz_wi("GICD", off);
    return;
  }
  const std::size_t vm   = vm_of(slot);
  DistState&        dist = g_dist[vm];
  WriteResult       write{};
  bool              known = false;
  {
    sync::Guard guard{g_vm_lock[vm]}; // SPI banks race post/refill across cores
    if (call->write) {
      // SPIs holding a live EoI token must survive an ICPENDR1 clear;
      // the model enforces it, this glue only snapshots the token set
      // inside the same critical section.
      const std::uint32_t keep_pending = off == kGicdIcpendr1 ? pending_token_mask(g_spi_tokens[vm]) : 0U;
      write                            = dist_write(dist, off, call->size, call->value, keep_pending);
      known                            = write.known;
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
  // Fan reevaluation out only for writes that change deliverability or
  // routing — a Linux GICv3 probe issues ~100 cosmetic writes
  // (priority/config/group), each of which would otherwise cost every
  // vCPU of the VM a refill or a physical cross-core SGI. Fan out after
  // dropping the model lock; the SMP hook coalesces remote owner work
  // and local targets refill immediately.
  if (write.delivery) {
    for (std::size_t v = 0; v < guest_table()[vm].vcpus; ++v) {
      request_reevaluate(slot_of(vm, v));
    }
  }
}

// The decoded frame is the vCPU index within the ACCESSING guest's VM;
// unmapped frames complete RAZ/WI (the guest's TYPER walk stops at Last
// and never reaches them).
void redist_mmio(MmioCall* call, std::uint64_t off) noexcept {
  const std::size_t resident = resident_here();
  if (resident == kNoResident) { // unreachable: a guest cannot trap before its first switch-in
    if (!call->write) {
      call->value = 0;
    }
    log_raz_wi("GICR", off);
    return;
  }
  const std::size_t    vm    = vm_of(resident);
  const RedistFrameRef frame = decode_redist_frame(off, guest_table()[vm].vcpus);
  if (!frame.valid) {
    if (!call->write) {
      call->value = 0;
    }
    log_raz_wi("GICR", off);
    return;
  }
  const std::size_t   slot   = slot_of(vm, frame.vcpu);
  CpuState&           cpu    = g_cpu[slot];
  const std::uint64_t in_off = frame.offset;

  if (call->write) {
    WriteResult write{};
    {
      sync::Guard guard{g_vm_lock[vm]}; // sibling frames are cross-core writable
      write = redist_write(cpu.redist, in_off, call->size, call->value);
    }
    if (!write.known) {
      log_raz_wi("GICR", off);
      return;
    }
    if (write.delivery) {
      request_reevaluate(slot);
    }
    return;
  }
  MmioRead r;
  {
    sync::Guard guard{g_vm_lock[vm]}; // sibling frames are cross-core writable
    r = redist_read(cpu.redist, in_off, call->size, frame.id);
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
  g_vtr        = gic_virt::vtr(); // boot-immutable (homogeneous cores)
  g_vmcr_reset = arch::gicv3::vmcr_reset(g_vtr);
  // The LR count is a hardware-derived array index: clamp it to the
  // shadow size instead of trusting the 5-bit field's full range.
  g_lr_count = std::min(gic_virt::lr_count(), kMaxLrs);
  for (auto& hw : g_hw) {
    hw.vmcr = g_vmcr_reset;
  }
  for (std::size_t c = 0; c < cpu::kMaxCpus; ++c) {
    g_resident[c] = kNoResident;
  }

  console::write("vGIC: ");
  console::write_dec64(g_lr_count);
  console::write(" list registers, GICD/GICR emulation active\n");
}

void cpu_reset(std::size_t index) noexcept {
  {
    sync::Guard guard{g_vm_lock[vm_of(index)]}; // a sibling can MMIO this redistributor frame
    g_cpu[index] = CpuState{};
  }
  g_hw[index] = HwBank{.vmcr = g_vmcr_reset, .hcr = gic_virt::kIchHcrBase};
}

void cpu_save(std::size_t index) noexcept {
  drain_eois(index);
  CpuState& cpu = g_cpu[index];
  for (std::size_t i = 0; i < g_lr_count; ++i) {
    cpu.lr[i] = gic_virt::read_lr(i);
  }
  g_hw[index].vmcr = gic_virt::read_vmcr();
  g_hw[index].hcr  = gic_virt::read_hcr();
}

void cpu_restore(std::size_t index) noexcept {
  flush(index); // self-heal a notification that raced an off/on transition
  const CpuState& cpu = g_cpu[index];
  for (std::size_t i = 0; i < g_lr_count; ++i) {
    gic_virt::write_lr(i, cpu.lr[i]);
  }
  gic_virt::write_vmcr(g_hw[index].vmcr);
  gic_virt::write_hcr(g_hw[index].hcr);
  resident_here() = index;
}

auto post_private(std::size_t index, std::uint32_t vintid) noexcept -> bool {
  if (vintid >= kNumPrivate) {
    return false;
  }
  {
    sync::Guard guard{g_vm_lock[vm_of(index)]}; // pending RMW races sibling-frame MMIO
    g_cpu[index].redist.pending |= 1U << vintid;
  }
  trace_emit(NOVA_TRACE_EV_VGIC_PRIVATE, static_cast<std::uint32_t>(index), vintid);
  flush(index);
  return true;
}

auto spi_band() noexcept -> SpiBand {
  return {.lo = kNumPrivate, .hi = kMaxIntid - 1};
}

auto post_spi(std::size_t vm, std::uint32_t vintid) noexcept -> bool {
  if (vintid < kNumPrivate || vintid >= kMaxIntid || vm >= guest_table().size()) {
    return false;
  }
  std::size_t target = 0;
  {
    // Publish pending and read the route in one critical section — an
    // injector-side route snapshot could go stale against a concurrent
    // IROUTER write. Notify that current target after dropping the lock.
    sync::Guard guard{g_vm_lock[vm]};
    g_dist[vm].spi_pending |= 1U << (vintid - kNumPrivate);
    target = slot_of(vm, spi_target(g_dist[vm], vintid, guest_table()[vm].vcpus));
  }
  // No token: nothing physical is behind this one, and the absence is
  // the fact — only post_spi_tracked binds one.
  trace_emit(NOVA_TRACE_EV_VGIC_POST, static_cast<std::uint32_t>(vm), vintid);
  request_reevaluate(target);
  return true;
}

auto post_spi_tracked(std::size_t vm, std::uint32_t vintid, std::uint32_t physical_intid,
                      std::uint64_t generation) noexcept -> bool {
  if (vintid < kNumPrivate || vintid >= kMaxIntid || vm >= guest_table().size() || generation == 0U) {
    return false;
  }
  std::size_t target = 0;
  {
    sync::Guard       guard{g_vm_lock[vm]};
    const std::size_t spi_index = vintid - kNumPrivate;
    EoiToken&         token     = g_spi_tokens[vm][spi_index];
    if (token.valid()) {
      return token.physical_intid == physical_intid && token.generation == generation;
    }
    token = {.virtual_intid = vintid, .physical_intid = physical_intid, .generation = generation};
    g_dist[vm].spi_pending |= 1U << spi_index;
    target = slot_of(vm, spi_target(g_dist[vm], vintid, guest_table()[vm].vcpus));
  }
  // Both INTIDs in one word, physical in the high half: the binding is
  // the whole content of this event.
  trace_emit(NOVA_TRACE_EV_VGIC_BIND, static_cast<std::uint32_t>(vm),
             vintid | (static_cast<std::uint64_t>(physical_intid) << 32U), generation);
  request_reevaluate(target);
  return true;
}

auto reevaluate(std::size_t index) noexcept -> bool {
  flush(index);
  return has_deliverable(index);
}

void vm_reset(std::size_t vm) noexcept {
  sync::Guard guard{g_vm_lock[vm]};
  g_dist[vm]       = DistState{};
  g_spi_tokens[vm] = {};
}

auto has_deliverable(std::size_t index) noexcept -> bool {
  // This is the wfi wake predicate — it runs on every guest wfi and on
  // every post to a blocked target. A pending LR is real (HCR_EL2.TWI
  // traps even a wfi that would complete immediately) and answers
  // without touching the shared distributor bank, so judge the LR
  // shadow first and take the lock only when it is empty.
  sync_resident_lrs(index);
  CpuState& cpu = g_cpu[index];
  for (std::size_t i = 0; i < g_lr_count; ++i) {
    if ((cpu.lr[i] & kLrStatePending) != 0U) {
      return true;
    }
  }
  const std::size_t vm = vm_of(index);
  sync::Guard       guard{g_vm_lock[vm]};
  return deliverable(cpu.redist) != 0U ||
         spi_deliverable(g_dist[vm], static_cast<std::uint32_t>(vcpu_of(index)), guest_table()[vm].vcpus) != 0U;
}

} // namespace nova::vgic

namespace nova {

// Injection state is the only route to it: the gdb stub's register set
// carries no ICH_*, so the EL2 shadow is all there is.
void vgic_component::telemetry(TelemetryCall* call) noexcept {
  call->declare(&vgic::g_cpu, sizeof vgic::g_cpu);
  call->declare(&vgic::g_spi_tokens, sizeof vgic::g_spi_tokens);
  call->declare(&vgic::g_dist, sizeof vgic::g_dist);
  call->declare(&vgic::g_resident, sizeof vgic::g_resident);
  call->declare(&vgic::g_lr_count, sizeof vgic::g_lr_count);
}

void vgic_component::handle_mmio(MmioCall* call) noexcept {
  if (call->ipa >= vgic::kGicdIpaBase && call->ipa < vgic::kGicdIpaBase + vgic::kGicdFrameSize) {
    call->handled = true;
    vgic::dist_mmio(call, call->ipa - vgic::kGicdIpaBase);
    return;
  }
  if (call->ipa >= vgic::kGicrIpaBase && call->ipa < vgic::kGicrIpaBase + kMaxVcpusPerVm * vgic::kGicrFrameSize) {
    call->handled = true;
    vgic::redist_mmio(call, call->ipa - vgic::kGicrIpaBase);
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
    // ICC_CTLR_EL1: mirror the implemented PRIbits/IDbits from ICH_VTR
    // (EOImode/CBPR stay 0); nothing writable we honor. A zero answer
    // would tell the guest "one priority bit, 16-bit INTIDs".
    if (s.crn == 12 && s.crm == 12 && s.op2 == 4) {
      call->handled = true;
      if (!s.write && s.rt != esr::kSrtZeroReg) {
        call->ctx->x[s.rt] = arch::gicv3::icc_ctlr_view(vgic::g_vtr);
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
  if (call->handled) {
    return;
  }
  if (call->intid != gic_virt::kMaintenanceIntid) {
    return;
  }
  call->handled              = true;
  const std::size_t resident = vgic::resident_here();
  vgic::drain_eois(resident);
  // Underflow: the guest resident on the receiving core drained its
  // LRs while software pending remained — top them up. flush() drops
  // UIE once the queue is empty, deasserting this (level) interrupt.
  vgic::flush(resident);
}

} // namespace nova
