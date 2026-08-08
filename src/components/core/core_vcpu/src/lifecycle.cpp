// components/core_vcpu/src/lifecycle.cpp
//
// VM lifecycle state machine: the seeding of a fresh execution context,
// cold start, warm reset, and per-vCPU power transitions. Start and
// reset are split into prepare/publish halves so device isolation can
// adopt the new boot generation while every vCPU is still off; the
// published power state is the only view other cores get.
//
// Index model: every entry point takes a flat vCPU slot
// (nova/abi/guest.hpp slot math). Per-VM state — Stage 2, the restart
// budget, the watchdog deadline — keys on vm_of(slot) and is owned by
// vcpu 0's affinity core.

#include "console_mux/console_mux.hpp"
#include "core_mmu/core_mmu.hpp"
#include "core_vcpu/core_vcpu.hpp"
#include "hal/console.hpp"
#include "hal/cpu.hpp"
#include "hal/timer.hpp"
#include "nova/abi/guest.hpp"
#include "nova/fmt.hpp"
#include "nova/sync.hpp"
#include "soft_timer/soft_timer.hpp"
#include "vcpu_internal.hpp"
#include "vgic/vgic.hpp"

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <string_view>

namespace nova::vcpu {

std::size_t                          g_count = 0;
lifecycle::RestartBudget<kMaxGuests> g_budget;

std::array<std::atomic<PowerState>, kMaxVcpus> g_published_state{};

std::array<std::atomic<std::uint64_t>, kMaxGuests> g_cntvoff{};

std::array<std::atomic<std::uint64_t>, kMaxGuests> g_vm_generation{};

std::atomic<std::size_t> g_lifecycle_transitions{0};

std::array<std::uint8_t, kMaxVcpus> g_affinity{};
std::array<bool, kMaxVcpus>         g_slot_valid{};

namespace {

void advance_vm_generation(std::size_t vm) noexcept {
  (void)sync::next_nonzero(g_vm_generation[vm]); // the new generation is read back via vm_generation()
}

// Reset a vCPU to a fresh execution context at `entry`. GP registers
// are zeroed so no EL2 (or previous-guest) values leak into it; x0
// carries `arg` (PSCI CPU_ON context_id — zero for a descriptor boot).
// Affinity-agnostic: touches only the slot's own banks, so the boot
// core may run it for every slot.
void seed_context(std::size_t slot, std::uint64_t entry, std::uint64_t sp, std::uint64_t arg) noexcept {
  Vcpu& v    = g_vcpus[slot];
  v.guest    = &guest_table()[vm_of(slot)];
  v.ctx      = TrapContext{};
  v.ctx.elr  = entry;
  v.ctx.sp   = sp;
  v.ctx.x[0] = arg;
  v.ctx.spsr = kSpsrEl1h;
  v.el1      = arch::El1SysregBank{};
  v.fp       = arch::FpBank{};
}

// Owner-local half of a reseed: FP ownership, the vGIC bank, and this
// core's parked-CNTV mirror. Every caller runs on the slot's affinity
// core (the lifecycle entry points guard it) — the timer slot lives in
// the calling core's queue, so running this elsewhere would cancel the
// wrong queue's entry.
void reseed_owner_state(std::size_t slot) noexcept {
  // Whatever this vCPU owned in the hardware FP file is garbage now —
  // drop ownership so no one ever saves it over a fresh bank.
  me().fp.invalidate(slot);
  vgic::cpu_reset(slot);
  // A reseeded vCPU owes nothing to its past life: drop the parked-CNTV
  // wake-up mirror (a fresh bank has CNTV disabled).
  soft_timer::cancel(soft_timer::kSlotCntvWake + slot);
}

void seed(std::size_t slot, std::uint64_t entry, std::uint64_t sp, std::uint64_t arg) noexcept {
  seed_context(slot, entry, sp, arg);
  reseed_owner_state(slot);
}

// Descriptor boot state: vcpu 0's cold/warm entry, x0 = the guest's
// DTB IPA (Linux boot protocol shape). Secondary vCPUs are seeded by
// CPU_ON with a caller-supplied entry and x0 = context_id instead
// (SP is the guest's own business there — PSCI leaves it undefined).
void seed_boot_context(std::size_t slot) noexcept {
  const GuestDescriptor& guest = guest_table()[vm_of(slot)];
  seed_context(slot, guest.entry_pc, guest.stack_top, guest.dtb_size != 0 ? guest.dtb_ipa : 0);
}

void seed_boot(std::size_t slot) noexcept {
  seed_boot_context(slot);
  reseed_owner_state(slot);
}

// True while a vCPU of `vm` is on or has a start reserved. `except`
// names a slot to ignore: a pending target must exclude itself when
// validating that its VM is already alive (CPU_ON) or entirely retired
// (cold VM_START). kMaxVcpus is never a valid slot, so the default
// considers every vCPU.
[[nodiscard]] auto vm_has_live(std::size_t vm, std::size_t except = kMaxVcpus) noexcept -> bool {
  for (std::size_t v = 0; v < guest_table()[vm].vcpus; ++v) {
    const std::size_t slot = slot_of(vm, v);
    if (slot != except && published_power(slot) != PowerState::kOff) {
      return true;
    }
  }
  return false;
}

} // namespace

auto vm_on(std::size_t vm) noexcept -> bool {
  return vm < guest_table().size() && vm_has_live(vm);
}

auto vm_generation(std::size_t vm) noexcept -> std::uint64_t {
  return vm < guest_table().size() ? g_vm_generation[vm].load(std::memory_order_acquire) : 0;
}

void begin_lifecycle_transition() noexcept {
  g_lifecycle_transitions.fetch_add(1, std::memory_order_acq_rel);
}

void end_lifecycle_transition() noexcept {
  g_lifecycle_transitions.fetch_sub(1, std::memory_order_acq_rel);
}

void init() noexcept {
  const auto        guests = guest_table();
  const std::size_t vms    = guests.size() <= kMaxGuests ? guests.size() : kMaxGuests; // core_mmu panicked if over
  g_count                  = vms * kMaxVcpusPerVm;
  for (std::size_t i = 0; i < g_count; ++i) {
    g_affinity[i]   = guests[vm_of(i)].cpu[vcpu_of(i)];
    g_slot_valid[i] = vcpu_of(i) < guests[vm_of(i)].vcpus;
  }
  for (std::size_t i = 0; i < kMaxVcpus; ++i) {
    g_published_state[i].store(PowerState::kOff, std::memory_order_relaxed);
  }
  for (std::size_t vm = 0; vm < kMaxGuests; ++vm) {
    g_cntvoff[vm].store(0, std::memory_order_relaxed);
    g_vm_generation[vm].store(0, std::memory_order_relaxed);
  }
  // Context-only: the boot core must not touch other cores' FP
  // ownership or timer queues, and every bank is already in its
  // zero-initialized reset state here.
  for (std::size_t i = 0; i < g_count; ++i) {
    if (valid_slot(i)) {
      seed_boot_context(i);
    }
  }
  g_vcpus[slot_of(0)].state = sched::State::kReady;
  advance_vm_generation(0);
  publish_power(slot_of(0), PowerState::kOn);
  publish_cntvoff(0, hyp_timer::now());
  g_alive.store(1, std::memory_order_relaxed); // the boot guest's vcpu 0
  g_slice_ticks.store(hyp_timer::ms_to_ticks(kSliceMs), std::memory_order_relaxed);
  console_mux::set_liveness_probe(&vcpu_on); // focus cycling skips off VMs
  seed_fp_trap(true);                        // no owner yet — first FP use claims the file
}

// Drop every per-VM device model before a (re)boot: the vGIC SPI bank
// directly (a declared dependency of this component), the VM's console
// line buffers, and — through VmResetService — any emulated device
// (vuart RX FIFO/masks) without this file naming it.
namespace {
void reset_vm_devices(std::size_t vm) noexcept {
  vgic::vm_reset(vm); // SPI banks are VM-global — per-vCPU cpu_reset misses them
  console_mux::vm_reset(vm);
  VmResetCall call{.vm = vm};
  cib::service<VmResetService>(&call);
}
} // namespace

auto reserve_start(std::size_t slot) noexcept -> bool {
  if (!valid_slot(slot)) {
    return false;
  }
  begin_lifecycle_transition();
  PowerState expected = PowerState::kOff;
  if (g_published_state[slot].compare_exchange_strong(expected, PowerState::kOnPending, std::memory_order_acq_rel)) {
    return true;
  }
  end_lifecycle_transition();
  return false;
}

void cancel_start(std::size_t slot) noexcept {
  if (!valid_slot(slot)) {
    return;
  }
  PowerState expected = PowerState::kOnPending;
  if (g_published_state[slot].compare_exchange_strong(expected, PowerState::kOff, std::memory_order_acq_rel)) {
    end_lifecycle_transition();
  }
}

auto prepare_start_vm(std::size_t vm) noexcept -> std::uint64_t {
  const std::size_t slot = slot_of(vm);
  if (vm >= vm_of(g_count) || affinity(slot) != cpu::id() || g_vcpus[slot].state != sched::State::kOff ||
      published_power(slot) != PowerState::kOnPending || vm_has_live(vm, slot)) {
    cancel_start(slot);
    return 0; // foreign-affinity starts arrive through the smp cross-call
  }
  reset_vm_devices(vm);
  soft_timer::cancel(soft_timer::kSlotWatchdog + vm);
  publish_cntvoff(vm, hyp_timer::now());
  seed_boot(slot);
  g_budget.refill(vm); // cold start — fresh warm-reset budget
  advance_vm_generation(vm);
  return vm_generation(vm);
}

auto publish_start_vm(std::size_t vm, std::uint64_t generation) noexcept -> bool {
  const std::size_t slot = slot_of(vm);
  if (vm >= vm_of(g_count) || generation == 0U || generation != vm_generation(vm) || affinity(slot) != cpu::id() ||
      g_vcpus[slot].state != sched::State::kOff || published_power(slot) != PowerState::kOnPending ||
      vm_has_live(vm, slot)) {
    cancel_start(slot);
    return false;
  }
  g_vcpus[slot].state = sched::State::kReady;
  publish_power(slot, PowerState::kOn);
  g_alive.fetch_add(1, std::memory_order_acq_rel);
  end_lifecycle_transition();
  reschedule_slice(); // the resident VCPU just gained a competitor
  return true;
}

auto renew_preboot_generation(std::size_t vm) noexcept -> std::uint64_t {
  const std::size_t slot = slot_of(vm);
  if (g_scheduler_started || vm >= vm_of(g_count) || affinity(slot) != cpu::id() ||
      g_vcpus[slot].state != sched::State::kReady || published_power(slot) != PowerState::kOn ||
      vm_has_live(vm, slot)) {
    return 0;
  }

  reset_vm_devices(vm);
  publish_cntvoff(vm, hyp_timer::now());
  seed_boot(slot);
  advance_vm_generation(vm);
  return vm_generation(vm);
}

auto start_vcpu(std::size_t slot, std::uint64_t entry, std::uint64_t context_id) noexcept -> bool {
  if (!valid_slot(slot)) {
    return false;
  }
  if (affinity(slot) != cpu::id() || g_vcpus[slot].state != sched::State::kOff ||
      published_power(slot) != PowerState::kOnPending || !vm_has_live(vm_of(slot), slot)) {
    cancel_start(slot);
    return false; // the VM itself has retired — nothing to join
  }
  seed(slot, entry, /*sp=*/0, context_id);
  g_vcpus[slot].state = sched::State::kReady;
  if (vcpu_of(slot) == 0) {
    soft_timer::cancel(soft_timer::kSlotWatchdog + vm_of(slot));
    advance_vm_generation(vm_of(slot));
  }
  publish_power(slot, PowerState::kOn);
  g_alive.fetch_add(1, std::memory_order_acq_rel);
  end_lifecycle_transition();
  reschedule_slice();
  return true;
}

// Retire one vCPU (CPU_OFF, VM-wide stop fan-out). The watchdog belongs
// to the boot vCPU's core, so retiring vCPU 0 always disarms it there.
auto retire_vcpu(std::size_t slot) noexcept -> bool {
  if (!valid_slot(slot) || affinity(slot) != cpu::id()) {
    return false;
  }
  if (g_vcpus[slot].state == sched::State::kOff) {
    cancel_start(slot); // quiesce also cancels an accepted CPU_ON not yet executed
    return false;
  }
  const bool was_current    = slot == me().current;
  g_vcpus[slot].state       = sched::State::kOff;
  const PowerState previous = g_published_state[slot].exchange(PowerState::kOff, std::memory_order_acq_rel);
  soft_timer::cancel(soft_timer::kSlotCntvWake + slot);
  if (vcpu_of(slot) == 0) {
    soft_timer::cancel(soft_timer::kSlotWatchdog + vm_of(slot));
  }
  if (previous == PowerState::kOn) {
    g_alive.fetch_sub(1, std::memory_order_acq_rel);
  }
  if (was_current) {
    me().current = kNoVcpu;
    reschedule_slice();
  }
  return was_current;
}

auto prepare_reset_quiesced_vm(std::size_t vm) noexcept -> std::uint64_t {
  const std::size_t slot = slot_of(vm);
  if (vm >= vm_of(g_count) || affinity(slot) != cpu::id() || vm_has_live(vm)) {
    return 0; // restore is legal only on the boot owner after every ACK
  }

  if (!g_budget.take(vm)) {
    console::write("[core_vcpu] VM ");
    console::write_dec64(vm);
    console::write(" restart budget exhausted — stopping\n");
    soft_timer::cancel(soft_timer::kSlotWatchdog + vm); // no reset from beyond the grave
    return 0;
  }

  const std::uint64_t restore_start = hyp_timer::now();
  const auto          restored      = mmu::reload_guest_image(vm);
  // Elapsed ticks → ms. The boot contract gate refuses a zero timebase,
  // so the divisor is non-zero here; guarding anyway keeps this reporting
  // path from being the one place a future ungated caller divides by it.
  const std::uint64_t hz         = hyp_timer::freq();
  const std::uint64_t restore_ms = hz != 0 ? (hyp_timer::now() - restore_start) * 1000U / hz : 0;
  fmt::DecBuf         vm_text{};
  fmt::DecBuf         written_text{};
  fmt::DecBuf         examined_text{};
  fmt::DecBuf         elapsed_text{};
  using namespace std::string_view_literals;
  console::write_parts(std::array{"[core_vcpu] VM "sv, fmt::to_dec64(vm, vm_text), " restored "sv,
                                  fmt::to_dec64(restored.written_bytes, written_text), "/"sv,
                                  fmt::to_dec64(restored.examined_bytes, examined_text), " bytes in "sv,
                                  fmt::to_dec64(restore_ms, elapsed_text), " ms\n"sv});
  reset_vm_devices(vm);
  publish_cntvoff(vm, hyp_timer::now());
  seed_boot(slot);
  soft_timer::cancel(soft_timer::kSlotWatchdog + vm); // the reboot re-opts in with its next heartbeat

  advance_vm_generation(vm);
  return vm_generation(vm);
}

auto publish_reset_vm(std::size_t vm, std::uint64_t generation) noexcept -> bool {
  const std::size_t slot = slot_of(vm);
  if (vm >= vm_of(g_count) || generation == 0U || generation != vm_generation(vm) || affinity(slot) != cpu::id() ||
      g_vcpus[slot].state != sched::State::kOff || vm_has_live(vm)) {
    return false;
  }

  g_vcpus[slot].state = sched::State::kReady;
  publish_power(slot, PowerState::kOn);
  g_alive.fetch_add(1, std::memory_order_acq_rel);
  reschedule_slice();
  console::write("[core_vcpu] VM ");
  console::write_dec64(vm);
  console::write(" reset state ready\n");
  return true;
}

} // namespace nova::vcpu
