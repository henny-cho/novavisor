// components/smp/src/smp.cpp
//
// Secondary bring-up sequence. The primary has finished every shared-state
// init (BSS, Stage 2 tables, GICD) before CPU_ON is issued, so a secondary
// only initializes what is banked per core, then enters its own scheduler.

#include "smp/smp.hpp"

#include "core_mmu/core_mmu.hpp"
#include "core_timer/core_timer.hpp"
#include "hal/cache.hpp"
#include "hal/console.hpp"
#include "hal/cpu.hpp"
#include "hal/gic.hpp"
#include "hal/timer.hpp"
#include "nova/abi/guest.hpp"
#include "nova/abi/psci.h"
#include "nova/panic.hpp"
#include "smp_internal.hpp"
#include "soft_timer/soft_timer.hpp"
#include "vgic/vgic.hpp"

#include <atomic>
#include <cstddef>
#include <cstdint>

// boot.S label — PSCI CPU_ON entry point (EL2 runs flat, PC == PA).
extern "C" void nova_secondary_entry() noexcept;

namespace nova::smp {

namespace {
// The boot PE's CTR_EL0 — every guest-memory CMO loop derives its line
// size and maintenance shortcuts from the executing PE's CTR, which is
// only sound while all PEs agree (homogeneous cluster). Secondaries
// verify against this and park on mismatch instead of running with
// under-scoped maintenance.
std::uint64_t g_boot_ctr = 0;
} // namespace

void start_secondaries() noexcept {
  const auto entry = reinterpret_cast<std::uint64_t>(&nova_secondary_entry);

  g_boot_ctr = cache::ctr();
  g_online[0].store(true, std::memory_order_release);
  gic::enable_ppi(kCrossCallSgi); // the primary receives cross-calls too

  for (std::size_t i = 1; i < cpu::kMaxCpus; ++i) {
    const std::uint64_t ret = cpu::smc_call(PSCI_FN_CPU_ON | PSCI_FN_SMC64, i, entry, i);
    if (ret != PSCI_SUCCESS) {
      console::write("[smp] core ");
      console::write_dec64(i);
      console::write(" CPU_ON failed — continuing without it\n");
      continue;
    }

    const std::uint64_t deadline = hyp_timer::deadline_after_ms(kOnlineWaitMs);
    while (!g_online[i].load(std::memory_order_acquire) && hyp_timer::now() < deadline) {
      // secondary is booting
    }
    if (!g_online[i].load(std::memory_order_acquire)) {
      console::write("[smp] core ");
      console::write_dec64(i);
      console::write(" did not come online\n");
    }
  }

  // With every core online, bring up the VMs configured to boot on
  // their own — unmodified guest OSes never issue HVC_VM_START for
  // their neighbors. VM 0 already boots via the scheduler init;
  // foreign-affinity VMs go through the regular cross-call path.
  for (std::size_t vm = 1; vm < guest_table().size(); ++vm) {
    if (guest_table()[vm].auto_start && !start_vm(vm)) {
      console::write("[smp] VM ");
      console::write_dec64(vm);
      console::write(" autostart failed\n");
    }
  }
}

} // namespace nova::smp

// C entry for secondaries (from boot.S nova_secondary_entry, on this
// core's own stack, vectors installed). Brings up everything banked
// per PE, reports online, and enters this core's scheduler.
extern "C" [[noreturn]] void novavisor_secondary(std::uint64_t cpu_index) noexcept {
  using namespace nova;

  // Homogeneous-cluster premise: this core's cache geometry must match
  // the boot PE's, or every CMO issued here is potentially under-scoped.
  if (cache::ctr() != smp::g_boot_ctr) {
    console::write("[smp] core ");
    console::write_dec64(cpu_index);
    console::write(" CTR_EL0 mismatch (heterogeneous cluster) — parking\n");
    halt();
  }

  gic::init_cpu();                     // redistributor + ICC
  vgic::init_cpu();                    // ICH + maintenance PPI
  hyp_timer::init();                   // CNTHCTL/CNTVOFF/CNTHP
  soft_timer::init();                  // CNTHP PPI enable
  gic::enable_ppi(kGuestTimerVintid);  // native guest CNTV
  gic::enable_ppi(smp::kCrossCallSgi); // cross-call mailbox
  mmu::activate_cpu();                 // VTCR/HCR — Stage 2 for this PE

  console::write("[smp] core ");
  console::write_dec64(cpu_index);
  console::write(" online\n");
  smp::g_online[cpu_index].store(true, std::memory_order_release);

  vcpu::enter_cpu();
}
