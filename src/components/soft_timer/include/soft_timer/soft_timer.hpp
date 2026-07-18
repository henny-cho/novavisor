#pragma once

// components/soft_timer/include/soft_timer/soft_timer.hpp
//
// Multiplexes the single EL2 physical one-shot (CNTHP, PPI 26) into
// fixed software deadline slots (timer_queue.hpp). Slot owners:
//   kSlotSlice        scheduler time slice (core_vcpu)
//   kSlotLegacyTimer  HVC_TIMER_SET one-shot (core_timer)
//   kSlotCntvWake+i   parked-CNTV wake-up for blocked VCPU i (core_vcpu)
//
// Expiry runs inside IRQ dispatch: callbacks receive the live trap
// frame and may swap it (preemption). The hardware timer is
// reprogrammed to the earliest remaining deadline after every
// mutation, so clients never touch CNTHP directly.

#include "core_gic/core_gic.hpp"
#include "nova/abi/guest.hpp"
#include "soft_timer/timer_queue.hpp"

#include <cib/top.hpp>
#include <cstddef>
#include <cstdint>
#include <flow/flow.hpp>

namespace nova::soft_timer {

inline constexpr std::size_t kSlotSlice       = 0;
inline constexpr std::size_t kSlotLegacyTimer = 1;
inline constexpr std::size_t kSlotCntvWake    = 2; // + VCPU index, kMaxGuests wide
inline constexpr std::size_t kSlotCount       = kSlotCntvWake + kMaxGuests;

// Enable the CNTHP PPI at the GIC (RuntimeStart).
void init() noexcept;

// Arm (or overwrite) `slot` to run `fn(ctx, arg)` once the physical
// counter reaches `deadline` (absolute; a past deadline fires
// immediately).
void arm(std::size_t slot, std::uint64_t deadline, Callback fn, std::uint64_t arg) noexcept;
void cancel(std::size_t slot) noexcept;

} // namespace nova::soft_timer

namespace nova {

struct soft_timer_component {
  // Claims CNTHP expiry (PPI 26): drains due slots, then re-arms to
  // the earliest remaining deadline.
  static void handle_irq(IrqCall* call) noexcept;

  // Touches the redistributor core_gic_component::INIT wakes, and
  // expects CNTHP disarmed by core_timer_component::INIT — list
  // soft_timer after both in the project's cib::components<>.
  constexpr static auto INIT = flow::action<"soft_timer_init">([]() noexcept { soft_timer::init(); });

  constexpr static auto config =
      cib::config(cib::extend<cib::RuntimeStart>(*INIT), cib::extend<IrqService>(&soft_timer_component::handle_irq));
};

} // namespace nova
