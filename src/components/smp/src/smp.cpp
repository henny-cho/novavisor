// components/smp/src/smp.cpp
//
// Secondary bring-up sequence and the cross-core mailbox. The primary
// has finished every shared-state init (BSS, Stage 2 tables, GICD)
// before CPU_ON is issued, so a secondary only initializes what is
// banked per core, then enters its own scheduler.

#include "smp/smp.hpp"

#include "hal/console.hpp"
#include "hal/cpu.hpp"
#include "hal/gic.hpp"
#include "hal/timer.hpp"
#include "nova/abi/guest.hpp"
#include "nova/abi/hvc_abi.h"
#include "nova/abi/psci.h"
#include "nova/arch/trap_context.hpp"
#include "nova/sync.hpp"

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>

// boot.S label — PSCI CPU_ON entry point (EL2 runs flat, PC == PA).
extern "C" void nova_secondary_entry() noexcept;

namespace nova::smp {
namespace {

// Physical SGI announcing "your mailbox has work" — EL2's own IPI.
// Guests never see physical SGIs (they get vINTIDs via ICH_LR).
inline constexpr std::uint32_t kCrossCallSgi = 0;

enum class Op : std::uint8_t { kStartVm, kPostVirq };

struct Request {
  Op            op = Op::kStartVm;
  std::uint32_t a  = 0;
  std::uint32_t b  = 0;
};

// One mailbox per core, written by any core under its lock, drained by
// the owner in IRQ context. Capacity covers the realistic burst (a
// couple of VMs' worth of doorbells); a full box rejects the request.
struct Mailbox {
  sync::SpinLock         lock;
  std::array<Request, 8> req{};
  std::size_t            count = 0;
};

std::array<Mailbox, cpu::kMaxCpus> g_mail;

// Set by each secondary as its last bring-up step; the primary's
// bounded wait reads it. acquire/release pairs the secondary's init
// writes with the primary's continuation.
std::array<std::atomic<bool>, cpu::kMaxCpus> g_online{};

// Bounded wait for one core to report online.
inline constexpr std::uint64_t kOnlineWaitMs = 100;

[[nodiscard]] auto enqueue(std::size_t target_cpu, Request r) noexcept -> bool {
  Mailbox& box = g_mail[target_cpu];
  {
    sync::Guard guard{box.lock};
    if (box.count == box.req.size()) {
      return false; // burst beyond capacity — caller sees a rejected call
    }
    box.req[box.count++] = r;
  }
  gic::send_sgi(target_cpu, kCrossCallSgi);
  return true;
}

} // namespace

void start_secondaries() noexcept {
  const auto entry = reinterpret_cast<std::uint64_t>(&nova_secondary_entry);

  gic::enable_ppi(kCrossCallSgi); // the primary receives cross-calls too

  for (std::size_t i = 1; i < cpu::kMaxCpus; ++i) {
    const std::uint64_t ret = arch::smc_call(PSCI_FN_CPU_ON | PSCI_FN_SMC64, /*target mpidr=*/i, entry, /*context=*/i);
    if (ret != PSCI_SUCCESS) {
      console::write("[smp] core ");
      console::write_dec64(i);
      console::write(" CPU_ON failed — continuing without it\n");
      continue;
    }

    const std::uint64_t deadline = hyp_timer::now() + hyp_timer::freq() * kOnlineWaitMs / 1000U;
    while (!g_online[i].load(std::memory_order_acquire) && hyp_timer::now() < deadline) {
      // secondary is booting
    }
    if (!g_online[i].load(std::memory_order_acquire)) {
      console::write("[smp] core ");
      console::write_dec64(i);
      console::write(" did not come online\n");
    }
  }
}

auto start_vm(std::size_t index) noexcept -> bool {
  const auto guests = guest_table();
  if (index >= guests.size()) {
    return false;
  }
  if (guests[index].cpu == cpu::id()) {
    return vcpu::start_vm(index);
  }
  return enqueue(guests[index].cpu, {.op = Op::kStartVm, .a = static_cast<std::uint32_t>(index), .b = 0});
}

auto post_virq(std::size_t index, std::uint32_t vintid) noexcept -> bool {
  const auto guests = guest_table();
  if (index >= guests.size()) {
    return false;
  }
  if (guests[index].cpu == cpu::id()) {
    return vcpu::post_virq(index, vintid);
  }
  return enqueue(guests[index].cpu, {.op = Op::kPostVirq, .a = static_cast<std::uint32_t>(index), .b = vintid});
}

} // namespace nova::smp

namespace nova {

void smp_component::handle_hvc(HvcCall* call) noexcept {
  if (call->func_id != NOVA_HVC_FN_VM_START) {
    return; // not ours — leave unclaimed for other subscribers
  }
  call->handled   = true;
  call->ctx->x[0] = smp::start_vm(static_cast<std::size_t>(call->ctx->x[1])) ? 0 : kSmcccNotSupported;
}

void smp_component::handle_irq(IrqCall* call) noexcept {
  if (call->intid != smp::kCrossCallSgi) {
    return;
  }
  call->handled = true;

  // Copy the batch out first — executing under the lock would deadlock
  // against a sender targeting this core from another IRQ path.
  smp::Mailbox&               box = smp::g_mail[cpu::id()];
  std::array<smp::Request, 8> batch{};
  std::size_t                 n = 0;
  {
    sync::Guard guard{box.lock};
    n         = box.count;
    box.count = 0;
    batch     = box.req;
  }
  for (std::size_t i = 0; i < n; ++i) {
    const smp::Request& r = batch[i];
    switch (r.op) {
    case smp::Op::kStartVm:
      (void)vcpu::start_vm(r.a); // owner's verdict; the requester was told "accepted"
      break;
    case smp::Op::kPostVirq:
      (void)vcpu::post_virq(r.a, r.b);
      break;
    }
  }
}

} // namespace nova

// C entry for secondaries (from boot.S nova_secondary_entry, on this
// core's own stack, vectors installed). Brings up everything banked
// per PE, reports online, and enters this core's scheduler.
extern "C" [[noreturn]] void novavisor_secondary(std::uint64_t cpu_index) noexcept {
  using namespace nova;

  gic::init_cpu();                               // redistributor + ICC
  vgic::init_cpu();                              // ICH + maintenance PPI
  hyp_timer::init();                             // CNTHCTL/CNTVOFF/CNTHP
  soft_timer::init();                            // CNTHP PPI enable
  gic::enable_ppi(hyp_timer::kGuestTimerVintid); // native guest CNTV
  gic::enable_ppi(smp::kCrossCallSgi);           // cross-call mailbox
  mmu::activate_cpu();                           // VTCR/HCR — Stage 2 for this PE

  console::write("[smp] core ");
  console::write_dec64(cpu_index);
  console::write(" online\n");
  smp::g_online[cpu_index].store(true, std::memory_order_release);

  vcpu::enter_cpu();
}
