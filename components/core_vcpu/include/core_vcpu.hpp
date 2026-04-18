#pragma once

// components/core_vcpu/include/core_vcpu.hpp
//
// Phase 5 VCPU entry component. Registers on cib::MainLoop as the
// final action of the boot flow: populates ELR_EL2 / SP_EL1 /
// SPSR_EL2 from the static guest descriptor and ERETs into EL1.
//
// Once control transfers to EL1, return to EL2 happens only via
// trap (vec.S path). This component has no state and no runtime
// API — everything is compile-time wired.

#include <cib/top.hpp>
#include <flow/flow.hpp>

namespace nova::vcpu {

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
