#pragma once

// nova/arch/cpu_features.hpp
//
// Speculation-barrier feature discovery for the guest-facing SMCCC arch
// service and the boot report. The distinction the ID registers let us
// draw is the one SMCCC cares about: NOT_REQUIRED asserts "this PE is
// unaffected", which the hypervisor may only claim with evidence, while
// NOT_SUPPORTED states the plain fact that this conduit adds no
// mitigation of its own. Answering NOT_REQUIRED without reading the ID
// registers tells an affected guest to stop mitigating.
//
// NovaVisor applies no EL2-side sequence and exposes the real MIDR
// (VPIDR_EL2 = MIDR_EL1), so anything short of "provably unaffected"
// resolves to "the guest decides from its own tables".
//
// Pure and host-testable; the raw register values come in through the
// hal facade.

#include <cstdint>
#include <string_view>

namespace nova::arch {

// ID_AA64PFR0_EL1.CSV2 [59:56] — 0 means the property is not disclosed,
// so it is evidence of nothing.
[[nodiscard]] constexpr auto pfr0_csv2(std::uint64_t pfr0) noexcept -> std::uint64_t {
  return (pfr0 >> 56U) & 0xFU;
}

// ID_AA64PFR0_EL1.CSV3 [63:60] — 1 means data loaded under speculation
// with a permission fault cannot be used to form a side channel.
[[nodiscard]] constexpr auto pfr0_csv3(std::uint64_t pfr0) noexcept -> std::uint64_t {
  return (pfr0 >> 60U) & 0xFU;
}

// ID_AA64PFR1_EL1.CSV2_frac [35:32] — refines CSV2 == 1 upward.
[[nodiscard]] constexpr auto pfr1_csv2_frac(std::uint64_t pfr1) noexcept -> std::uint64_t {
  return (pfr1 >> 32U) & 0xFU;
}

// ID_AA64PFR1_EL1.SSBS [7:4] — non-zero means the PE carries the
// speculative-store-bypass control in PSTATE, so a guest needs no
// firmware call for it.
[[nodiscard]] constexpr auto pfr1_ssbs(std::uint64_t pfr1) noexcept -> std::uint64_t {
  return (pfr1 >> 4U) & 0xFU;
}

// ID_AA64ISAR2_EL1.CLRBHB [31:28] — the branch-history clear
// instruction, which a guest can issue for itself.
[[nodiscard]] constexpr auto isar2_clrbhb(std::uint64_t isar2) noexcept -> bool {
  return ((isar2 >> 28U) & 0xFU) != 0U;
}

struct SpeculationState {
  std::uint64_t csv2      = 0;
  std::uint64_t csv2_frac = 0;
  std::uint64_t csv3      = 0;
  std::uint64_t ssbs      = 0;
  bool          clrbhb    = false;
};

[[nodiscard]] constexpr auto read_speculation_state(std::uint64_t pfr0, std::uint64_t pfr1,
                                                    std::uint64_t isar2) noexcept -> SpeculationState {
  return {.csv2      = pfr0_csv2(pfr0),
          .csv2_frac = pfr1_csv2_frac(pfr1),
          .csv3      = pfr0_csv3(pfr0),
          .ssbs      = pfr1_ssbs(pfr1),
          .clrbhb    = isar2_clrbhb(isar2)};
}

enum class Mitigation : std::uint8_t {
  kUnaffected,     // the PE reports the property — SMCCC NOT_REQUIRED is honest
  kGuestMitigates, // affected, and the guest has what it needs to act itself
  kUnknown,        // not disclosed — claim nothing
};

[[nodiscard]] constexpr auto to_string(Mitigation m) noexcept -> std::string_view {
  switch (m) {
  case Mitigation::kUnaffected:
    return "unaffected";
  case Mitigation::kGuestMitigates:
    return "guest";
  case Mitigation::kUnknown:
    return "unknown";
  }
  return "unknown";
}

// Branch-target injection. CSV2 >= 1 states that branch targets trained
// in one context cannot exploitably control speculation in another.
[[nodiscard]] constexpr auto branch_target_mitigation(const SpeculationState& s) noexcept -> Mitigation {
  return s.csv2 >= 1U ? Mitigation::kUnaffected : Mitigation::kUnknown;
}

// Speculative store bypass. SSBS in PSTATE is the guest's own control,
// so the firmware call is not what it should be relying on.
[[nodiscard]] constexpr auto store_bypass_mitigation(const SpeculationState& s) noexcept -> Mitigation {
  return s.ssbs != 0U ? Mitigation::kUnaffected : Mitigation::kUnknown;
}

// Branch-history injection. CSV2 == 3 (CSV2_1p2) covers the branch
// history buffer as well; CSV2 == 1 with frac >= 2 encodes the same
// level. CLRBHB alone means the guest can clear the history itself,
// which is a guest-side mitigation, not an unaffected PE.
[[nodiscard]] constexpr auto branch_history_mitigation(const SpeculationState& s) noexcept -> Mitigation {
  if (s.csv2 >= 3U || (s.csv2 == 1U && s.csv2_frac >= 2U)) {
    return Mitigation::kUnaffected;
  }
  return s.clrbhb ? Mitigation::kGuestMitigates : Mitigation::kUnknown;
}

// Data-cache side channel from faulting speculative loads. Reported for
// the boot log; SMCCC has no workaround call for it.
[[nodiscard]] constexpr auto fault_channel_mitigation(const SpeculationState& s) noexcept -> Mitigation {
  return s.csv3 >= 1U ? Mitigation::kUnaffected : Mitigation::kUnknown;
}

} // namespace nova::arch
