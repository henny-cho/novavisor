#pragma once

// components/smp/src/smp_internal.hpp
//
// Internal to the smp component: the state and declarations its
// translation units (cross-call transport, VM lifecycle, physical
// bring-up) share. It sits under src/ on purpose — the include/ tree
// stays the component's public surface, so peers cannot reach these.

#include "hal/cpu.hpp"
#include "nova/abi/guest.hpp"
#include "nova/sync.hpp"
#include "smp/quiesce_model.hpp"
#include "smp/smp.hpp"

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>

namespace nova::smp {

// Physical SGI announcing "your mailbox has work" — EL2's own IPI.
// Guests never see physical SGIs (they get vINTIDs via ICH_LR).
inline constexpr std::uint32_t kCrossCallSgi     = 0;
inline constexpr std::size_t   kMailboxCapacity  = 16;
inline constexpr std::size_t   kLifecycleReserve = 2 * kMaxGuests * (kMaxVcpusPerVm - 1);
static_assert(kLifecycleReserve < kMailboxCapacity);

enum class Op : std::uint8_t {
  kStartVm,
  kPostVirq,
  kCpuOn,
  kVmOwnerCall,
  kBeginReset,
  kBeginStop,
  kQuiesceVcpu,
  kQuiesceAck,
};

// `idx` is a VM for start/begin-reset/owner-call, a vCPU slot
// otherwise. a/b carry operation arguments or the reset epoch.
struct Request {
  Op            op       = Op::kStartVm;
  std::uint32_t idx      = 0;
  std::uint64_t a        = 0;
  std::uint64_t b        = 0;
  std::uint64_t c        = 0;
  VmOwnerCall   callback = nullptr;
};

// One mailbox per core, written by any core under its lock, drained by
// the owner in IRQ context. Capacity covers the realistic burst (a
// couple of VMs' worth of doorbells); a full box rejects the request.
struct Mailbox {
  sync::SpinLock                        lock;
  std::array<Request, kMailboxCapacity> req{};
  std::size_t                           count = 0;
};

extern std::array<Mailbox, cpu::kMaxCpus> g_mail;

extern std::array<std::atomic<std::uint32_t>, cpu::kMaxCpus> g_reevaluate;

// Set by each secondary as its last bring-up step; the primary's
// bounded wait reads it. acquire/release pairs the secondary's init
// writes with the primary's continuation.
extern std::array<std::atomic<bool>, cpu::kMaxCpus> g_online;

// Bounded wait for one core to report online.
inline constexpr std::uint64_t kOnlineWaitMs = 100;

// VM lifecycle state is owned by the boot vCPU's core. The atomic token
// serializes reset, stop, cold-start, and CPU_ON across cores.
extern std::array<lifecycle::QuiesceTracker<kMaxVcpusPerVm>, kMaxGuests> g_lifecycle;
extern std::array<std::atomic<std::uint64_t>, kMaxGuests>                g_lifecycle_token;

enum class LifecycleMode : std::uint8_t {
  kNone,
  kReset,
  kStop,
};

extern std::array<LifecycleMode, kMaxGuests> g_lifecycle_mode;
extern std::array<bool, kMaxGuests>          g_dma_pending;
extern std::array<bool, kMaxGuests>          g_dma_failed;

// Zero means inactive. The reserved value closes the race between a
// caller claiming ownership and the boot owner publishing the
// tracker epoch; remote quiesce commands carry the final epoch.
inline constexpr std::uint64_t kLifecycleInactive = 0;
inline constexpr std::uint64_t kLifecycleReserved = lifecycle::kUnpublishedEpoch;

// A cross-call should complete in microseconds, but emulation and
// heavily instrumented builds need margin. Three retries make lifecycle
// failure bounded to roughly 400 ms without spuriously isolating a VM.
inline constexpr std::uint64_t kQuiesceTimeoutMs = 100;
inline constexpr std::uint64_t kDmaPollMs        = 1;

struct BeginLifecycleResult {
  bool accepted          = false;
  bool schedule_required = false;
};

// A vCPU slot's owning core (per-vCPU affinity — not the VM's).
[[nodiscard]] inline auto slot_cpu(std::size_t slot) noexcept -> std::size_t {
  return guest_table()[vm_of(slot)].cpu[vcpu_of(slot)];
}

[[nodiscard]] inline auto valid_slot(std::size_t slot) noexcept -> bool {
  return vm_of(slot) < guest_table().size() && vcpu_of(slot) < guest_table()[vm_of(slot)].vcpus;
}

// Mailbox transport (cross_call.cpp).
[[nodiscard]] auto enqueue(std::size_t target_cpu, Request r, bool lifecycle = false) noexcept -> bool;

// Lifecycle coordinator entry points reached from the mailbox drain
// (vm_lifecycle.cpp).
[[nodiscard]] auto lifecycle_token(std::size_t vm) noexcept -> std::uint64_t;
[[nodiscard]] auto lifecycle_blocks_start(std::size_t vm) noexcept -> bool;
[[nodiscard]] auto start_vm_local(std::size_t vm) noexcept -> bool;
void               acknowledge_quiesce(std::size_t slot, std::uint64_t epoch) noexcept;
[[nodiscard]] auto begin_lifecycle_local(std::size_t vm, LifecycleMode mode) noexcept -> BeginLifecycleResult;

} // namespace nova::smp
