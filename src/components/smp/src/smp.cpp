// components/smp/src/smp.cpp
//
// Secondary bring-up sequence. The primary has finished every
// shared-state init (BSS, Stage 2 tables, GICD) before CPU_ON is
// issued, so the secondary only initializes what is banked per core.

#include "smp/smp.hpp"

#include "hal/console.hpp"
#include "hal/cpu.hpp"
#include "hal/gic.hpp"
#include "hal/timer.hpp"
#include "nova/abi/psci.h"

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>

// boot.S label — PSCI CPU_ON entry point (EL2 runs flat, PC == PA).
extern "C" void nova_secondary_entry() noexcept;

namespace nova::smp {
namespace {

// Set by each secondary as its last bring-up step; the primary's
// bounded wait reads it. acquire/release pairs the secondary's init
// writes with the primary's continuation.
std::array<std::atomic<bool>, cpu::kMaxCpus> g_online{};

// Bounded wait for one core to report online.
inline constexpr std::uint64_t kOnlineWaitMs = 100;

} // namespace

void start_secondaries() noexcept {
  const auto entry = reinterpret_cast<std::uint64_t>(&nova_secondary_entry);

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

} // namespace nova::smp

// C entry for secondaries (from boot.S nova_secondary_entry, on this
// core's own stack, vectors installed).
extern "C" [[noreturn]] void novavisor_secondary(std::uint64_t cpu_index) noexcept {
  using namespace nova;

  gic::init_cpu();
  hyp_timer::init();

  console::write("[smp] core ");
  console::write_dec64(cpu_index);
  console::write(" online\n");
  smp::g_online[cpu_index].store(true, std::memory_order_release);

  for (;;) {
    __asm__ volatile("wfi"); // scheduler entry lands here in the per-CPU scheduler step
  }
}
