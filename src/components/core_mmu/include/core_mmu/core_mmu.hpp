#pragma once

// components/core_mmu/include/core_mmu/core_mmu.hpp
//
// Stage 2 MMU CIB component.
//
// Extends cib::RuntimeStart. BSS (where our static L1/L2/L3 tables
// live) is cleared by boot.S before any C++ runs, so the build step
// can write valid descriptors without being zeroed afterwards.
//
// Effect: on boot, builds one Stage 2 table set per guest_table()
// entry (window PA slot + IVC shared page) and writes VTCR_EL2 /
// VTTBR_EL2 / HCR_EL2 for the boot guest. After this component's
// action completes, any subsequent ERET to EL1 sees its accesses
// translated through Stage 2.

#include <cib/top.hpp>
#include <cstddef>
#include <flow/flow.hpp>

namespace nova::mmu {

// Populate all per-guest table sets, program VTCR/VTTBR/HCR for guest
// [0], invalidate TLB. [[noreturn]]-safe — returns normally; the
// Stage 2 MMU is active on return, but has no effect until control
// transfers to EL1.
void init_and_activate() noexcept;

// Program this PE's banked Stage 2 registers (VTCR/VTTBR/HCR) from the
// tables the primary built. Secondaries run it once during bring-up;
// init_and_activate() already covers the primary.
void activate_cpu() noexcept;

// Retarget VTTBR_EL2 to another guest's table set (index into
// guest_table()). VMID tagging keeps the TLB coherent — no invalidation
// on the switch path. Called by the VCPU scheduler with a valid index.
void switch_vm(std::size_t guest_index) noexcept;

// Warm-reset support: init_and_activate() preserves every guest window
// in the pristine area (NOVA_GUEST_PRISTINE_PA — the loader ran before
// the CPU started, so the windows still hold unmodified images plus
// zeroed RAM); reload_guest_image() copies one window back. The IVC
// shared page lies outside the windows and is never touched.
void reload_guest_image(std::size_t guest_index) noexcept;

} // namespace nova::mmu

namespace nova {

struct core_mmu_component {
  constexpr static auto INIT = flow::action<"core_mmu_init">([]() noexcept { mmu::init_and_activate(); });

  constexpr static auto config = cib::config(cib::extend<cib::RuntimeStart>(*INIT));
};

} // namespace nova
