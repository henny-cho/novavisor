#pragma once

// components/core_vcpu/include/core_vcpu.hpp
//
// VCPU state and entry component. Registers on cib::MainLoop as the
// final action of the boot flow: seeds vcpu 0 from the guest table
// (nova/guest.hpp, provided by the project composition) and ERETs
// into EL1.
//
// Once control transfers to EL1, return to EL2 happens only via
// trap (vec.S path).

#include "nova/guest.hpp"

#include <cib/top.hpp>
#include <cstdint>
#include <flow/flow.hpp>

namespace nova {

// Per-VCPU runtime state, owned by core_vcpu. Phase 5/6 runs a single
// VCPU on the single physical core; Phase 7 (multi-VM) turns the
// backing store into one entry per guest-table row. Phase 6 components
// (timer, vGIC) hang their per-VCPU state off this struct.
struct Vcpu {
  enum class State : std::uint8_t { kOff, kRunning };

  const GuestDescriptor* guest = nullptr;
  std::uint64_t          elr   = 0; // EL1 resume PC (ELR_EL2 seed)
  std::uint64_t          sp    = 0; // initial SP_EL1
  std::uint64_t          spsr  = 0; // SPSR_EL2 restored on ERET
  State                  state = State::kOff;
};

} // namespace nova

namespace nova::vcpu {

// The VCPU currently executing on this physical core.
// Phase 5/6: always vcpu 0.
auto current() noexcept -> Vcpu&;

// Transfer to EL1 via ERET. [[noreturn]] — the only way back is a
// hardware trap.
[[noreturn]] void enter_guest() noexcept;

} // namespace nova::vcpu

namespace nova {

struct core_vcpu_component {
  constexpr static auto ENTER = flow::action<"core_vcpu_enter">([]() noexcept { vcpu::enter_guest(); });

  constexpr static auto config = cib::config(cib::extend<cib::MainLoop>(*ENTER));
};

} // namespace nova
