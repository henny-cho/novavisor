// components/watchdog/src/watchdog.cpp

#include "watchdog/watchdog.hpp"

#include "core_vcpu/core_vcpu.hpp"
#include "hal/console.hpp"
#include "hal/timer.hpp"
#include "nova/abi/guest.hpp"
#include "nova/abi/hvc_abi.h"
#include "nova/arch/trap_context.hpp"
#include "smp/smp.hpp"
#include "soft_timer/soft_timer.hpp"
#include "watchdog/watchdog_model.hpp"

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>

namespace nova {
namespace {

// Accessed only on each VM boot vCPU's owner core. The shared boot
// generation rejects delayed mailbox work from an older instance.
std::array<std::uint64_t, kMaxGuests>              g_armed_generation{};
std::array<std::uint64_t, kMaxGuests>              g_armed_sequence{};
std::array<std::atomic<std::uint64_t>, kMaxGuests> g_update_sequence{};

void on_expiry(TrapContext* ctx, std::uint64_t vm) noexcept;

[[nodiscard]] auto next_sequence(std::size_t vm) noexcept -> std::uint64_t {
  std::uint64_t current = g_update_sequence[vm].load(std::memory_order_relaxed);
  for (;;) {
    std::uint64_t next = current + 1U;
    if (next == 0) {
      next = 1;
    }
    if (g_update_sequence[vm].compare_exchange_weak(current, next, std::memory_order_acq_rel,
                                                    std::memory_order_relaxed)) {
      return next;
    }
  }
}

void apply_local(std::size_t vm, std::uint64_t deadline, std::uint64_t generation, std::uint64_t sequence) noexcept {
  if (vm >= guest_table().size() ||
      !watchdog::accepts_update(generation, vcpu::vm_generation(vm), sequence,
                                g_update_sequence[vm].load(std::memory_order_acquire),
                                vcpu::power_state(slot_of(vm)) == vcpu::PowerState::kOn)) {
    return;
  }

  g_armed_generation[vm] = generation;
  g_armed_sequence[vm]   = sequence;
  if (deadline == 0) {
    soft_timer::cancel(soft_timer::kSlotWatchdog + vm);
  } else {
    soft_timer::arm(soft_timer::kSlotWatchdog + vm, deadline, &on_expiry, vm);
  }
}

// Expiry runs in the soft_timer IRQ drain — even a guest hung in a
// busy loop is interruptible there (CNTHP is a physical IRQ, HCR.IMO
// routes it to EL2). The reset is VM-wide and affinity-routed: the
// hung VM's vcpu 0 may live on another core.
void on_expiry(TrapContext* ctx, std::uint64_t vm) noexcept {
  const auto index = static_cast<std::size_t>(vm);
  if (index >= guest_table().size() ||
      !watchdog::accepts_update(g_armed_generation[index], vcpu::vm_generation(index), g_armed_sequence[index],
                                g_update_sequence[index].load(std::memory_order_acquire),
                                vcpu::power_state(slot_of(index)) == vcpu::PowerState::kOn)) {
    return;
  }
  g_armed_generation[index] = 0;
  g_armed_sequence[index]   = 0;
  console::write("[watchdog] VM ");
  console::write_dec64(vm);
  console::write(" missed its heartbeat window — resetting\n");
  (void)smp::reset_vm(index, ctx, true);
}

} // namespace

void watchdog_component::handle_hvc(HvcCall* call) noexcept {
  if (call->func_id != NOVA_HVC_FN_HEARTBEAT) {
    return; // not ours — leave unclaimed for other subscribers
  }
  call->handled = true;

  const std::size_t   vm         = vm_of(vcpu::current_index());
  const std::uint64_t window_ms  = call->ctx->x[1];
  const auto          plan       = watchdog::deadline_after_ms(hyp_timer::now(), hyp_timer::freq(), window_ms);
  const std::uint64_t generation = vcpu::vm_generation(vm);
  if (!plan.accepted || vcpu::power_state(slot_of(vm)) != vcpu::PowerState::kOn) {
    call->ctx->x[0] = kSmcccNotSupported;
    return;
  }
  const std::uint64_t sequence = next_sequence(vm);
  if (!smp::invoke_vm_owner(vm, &apply_local, plan.deadline, generation, sequence)) {
    call->ctx->x[0] = kSmcccNotSupported;
    return;
  }
  call->ctx->x[0] = 0;
}

} // namespace nova
