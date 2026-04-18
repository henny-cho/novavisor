#pragma once

// components/core_mmu/include/core_mmu.hpp
//
// Stage 2 MMU CIB component.
//
// Extends cib::RuntimeStart — runs after EarlyRuntimeInit has cleared
// BSS (where our static L1/L2/L3 tables live), so the build step can
// write valid descriptors without being zeroed afterwards.
//
// Effect: on boot, identity-maps the Phase 5 guest window into Stage 2
// and writes VTCR_EL2 / VTTBR_EL2 / HCR_EL2. After this component's
// action completes, any subsequent ERET to EL1 sees its accesses
// translated through Stage 2.

#include <cib/top.hpp>
#include <flow/flow.hpp>

namespace nova::mmu {

// Populate L1/L2/L3 tables, program VTCR/VTTBR/HCR, invalidate TLB.
// [[noreturn]]-safe — returns normally; the Stage 2 MMU is active on
// return, but has no effect until control transfers to EL1.
void init_and_activate() noexcept;

} // namespace nova::mmu

namespace nova {

struct core_mmu_component {
  constexpr static auto INIT = flow::action<"core_mmu_init">([]() noexcept { mmu::init_and_activate(); });

  constexpr static auto config = cib::config(cib::extend<cib::RuntimeStart>(*INIT));
};

} // namespace nova
