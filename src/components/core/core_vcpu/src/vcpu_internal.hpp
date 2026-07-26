#pragma once

// components/core_vcpu/src/vcpu_internal.hpp
//
// Internal to the core_vcpu component: the state and declarations its
// translation units (per-core scheduler, VM lifecycle) share. It sits
// under src/ on purpose — the include/ tree stays the component's
// public surface, so peers cannot reach these.

#include "core_vcpu/core_vcpu.hpp"
#include "hal/cpu.hpp"
#include "nova/abi/guest.hpp"

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>

namespace nova::vcpu {

// SPSR_EL2 value to restore on ERET into a fresh guest:
//   M[3:0]   = 0b0101  EL1h  (EL1, using SP_EL1)
//   M[4]     = 0       AArch64 execution state
//   F (b6)   = 1       FIQ masked
//   I (b7)   = 1       IRQ masked
//   A (b8)   = 1       SError masked
//   D (b9)   = 1       Debug masked
//   others   = 0
// The guest unmasks I itself once its vector table is installed.
inline constexpr std::uint64_t kSpsrEl1h = 0x3C5ULL;

// Preemption quantum. Converted to counter ticks at init from the
// platform clock; board-specific tuning waits for a second board
// (single-source trigger discipline).
inline constexpr std::uint64_t kSliceMs = 10;

// "No VCPU resident on this core" — before the first guest entry and
// after every local guest retired.
inline constexpr std::size_t kNoVcpu = ~std::size_t{0};

// Per-core scheduler state: the resident VCPU, the ownership of this
// core's FP register file, and a mirror of the last CPTR_EL2.TFP value
// written so the switch path can skip the common no-change case.
struct CpuSched {
  std::size_t   current = kNoVcpu;
  fp::Ownership fp;
  bool          fp_trap = false; // meaningless until seed_fp_trap runs on this core
  bool          idling  = false; // inside schedule_out's wfi+drain loop (see schedule_after_retire)
};

extern std::array<Vcpu, kMaxVcpus>          g_vcpus;
extern std::size_t                          g_count;       // vCPU slots; boot-immutable after init()
extern std::uint64_t                        g_slice_ticks; // boot-immutable after init()
extern std::array<CpuSched, cpu::kMaxCpus>  g_sched;
extern lifecycle::RestartBudget<kMaxGuests> g_budget; // per-VM — micro-reboot is a VM-level policy

static_assert(std::atomic<PowerState>::is_always_lock_free);

// The scheduler's detailed state is owner-core only. Cross-core PSCI,
// reset fan-out, and console liveness use this minimal published view.
extern std::array<std::atomic<PowerState>, kMaxVcpus> g_published_state;

// Per-VM virtual-counter offset (CNTVCT = CNTPCT - offset), written on
// every switch-in like VTTBR. Cold start and warm reset re-base it to
// the current physical count, so a VM's virtual counter starts near
// zero on every (re)boot — a rebooted guest never sees time jump.
// A VM's vCPUs can run on different cores, so every switch-in reads a
// release-published value instead of racing an owner-core reset write.
extern std::array<std::atomic<std::uint64_t>, kMaxGuests> g_cntvoff;
static_assert(std::atomic<std::uint64_t>::is_always_lock_free);

// Incremented whenever a VM's boot vCPU enters a new powered-on
// instance. Cross-core watchdog requests use it as a stale-work token.
extern std::array<std::atomic<std::uint64_t>, kMaxGuests> g_vm_generation;
extern bool                                               g_scheduler_started;

// vCPUs not yet retired, machine-wide — the only scheduler state
// shared across cores. Each core idles on its own empty ready-set; the
// halt line is printed exactly once, by whichever core retires the
// last vCPU.
extern std::atomic<std::size_t> g_alive;
extern std::atomic<bool>        g_halt_announced;

// A reset can temporarily retire every VCPU while cross-core ACKs are
// still in flight; a remote start can also be accepted before g_alive
// is incremented on its owner. Keep idle schedulers draining IRQs until
// each transition publishes an on VCPU, rolls back, or gives up.
extern std::atomic<std::size_t> g_lifecycle_transitions;

[[nodiscard]] inline auto me() noexcept -> CpuSched& {
  return g_sched[cpu::id()];
}

// Boot-time caches of guest_table() facts the scheduler consults on
// every trap — the table is boot-immutable and the accessor is an
// out-of-line call (no LTO).
extern std::array<std::uint8_t, kMaxVcpus> g_affinity;
extern std::array<bool, kMaxVcpus>         g_slot_valid;

[[nodiscard]] inline auto affinity(std::size_t slot) noexcept -> std::size_t {
  return g_affinity[slot];
}

// Slots past a VM's vcpu count exist in the arrays but are never
// seeded, started, or picked — they stay kOff for the machine's life.
[[nodiscard]] inline auto valid_slot(std::size_t slot) noexcept -> bool {
  return slot < g_count && g_slot_valid[slot];
}

inline void publish_power(std::size_t slot, PowerState state) noexcept {
  g_published_state[slot].store(state, std::memory_order_release);
}

[[nodiscard]] inline auto published_power(std::size_t slot) noexcept -> PowerState {
  return g_published_state[slot].load(std::memory_order_acquire);
}

inline void publish_cntvoff(std::size_t vm, std::uint64_t value) noexcept {
  g_cntvoff[vm].store(value, std::memory_order_release);
}

[[nodiscard]] inline auto cntvoff(std::size_t vm) noexcept -> std::uint64_t {
  return g_cntvoff[vm].load(std::memory_order_acquire);
}

// Scheduler services the lifecycle transitions reach (sched.cpp).
void reschedule_slice() noexcept;
void seed_fp_trap(bool trap) noexcept;

} // namespace nova::vcpu
