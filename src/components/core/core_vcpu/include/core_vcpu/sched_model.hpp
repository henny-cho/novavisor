#pragma once

// components/core_vcpu/include/core_vcpu/sched_model.hpp
//
// Pure scheduler core — VCPU run states and the pick/predicate logic,
// host-testable. sched.cpp owns the hardware glue (frame swap, EL1
// bank, vGIC residency, Stage 2 retarget) around these decisions.

#include <cstddef>
#include <cstdint>
#include <span>

namespace nova::sched {

enum class State : std::uint8_t {
  kOff,     // never started, or exited
  kReady,   // runnable, waiting for the scheduler
  kRunning, // resident on the core
  kBlocked, // parked in wfi, waiting for a deliverable vIRQ
};

// Next kReady index after `current` in ring order; `current` itself is
// considered last (it may have been woken while scheduling out).
// Returns states.size() when nothing is runnable.
[[nodiscard]] inline auto pick_next(std::span<const State> states, std::size_t current) noexcept -> std::size_t {
  for (std::size_t step = 1; step <= states.size(); ++step) {
    const std::size_t idx = (current + step) % states.size();
    if (states[idx] == State::kReady) {
      return idx;
    }
  }
  return states.size();
}

// Whether a door may take the resident VCPU's shadow of the registers
// that live in hardware.
//
// Running is the whole guard against writing over state this core does
// not own: a slot between reseed and re-entry is kOff or kReady (every
// reseed path demands kOff and leaves kReady), and an idle core's frame
// is EL2 scratch rather than any guest's.
//
// `turn` is the publisher's last one and `taken` the turn this shadow
// already belongs to, so a shadow is refreshed once per published
// reading however often doors open — and never before the first turn,
// when nothing is reading yet.
[[nodiscard]] inline auto shadow_due(State state, std::uint64_t turn, std::uint64_t taken) noexcept -> bool {
  return state == State::kRunning && turn != taken;
}

// True when the resident VCPU has a runnable competitor — the
// condition for arming the preemption time slice.
[[nodiscard]] inline auto slice_needed(std::span<const State> states) noexcept -> bool {
  for (const State s : states) {
    if (s == State::kReady) {
      return true;
    }
  }
  return false;
}

} // namespace nova::sched
