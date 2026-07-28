#pragma once

// components/psci/include/psci/smccc_model.hpp
//
// Pure SMCCC Arch service dispatch (0x8000_xxxx), host-testable. This
// is the non-PSCI half of the firmware interface a guest expects: the
// calling-convention version, feature discovery, and the CPU
// speculation workarounds.
//
// The whole 0x8000_xxxx range is claimed so unimplemented IDs answer
// NOT_SUPPORTED instead of leaking "unknown HVC" warnings, and
// PSCI_FEATURES must report SMCCC_VERSION as present — guest Linux
// gates every SMCCC 1.1 call on that one probe.
//
// Workaround policy: NovaVisor exposes the real MIDR (VPIDR_EL2 =
// MIDR_EL1) and runs no EL2-side mitigation sequence, so a guest applies
// its own EL1-local mitigations from what it reads. Two answers are
// therefore available and they are not interchangeable. NOT_REQUIRED
// asserts *the PE is unaffected* — a claim about the hardware, which
// only the ID registers can support; a guest that believes it stops
// mitigating. NOT_SUPPORTED states the plain fact that this conduit
// carries no mitigation, which leaves the guest on its own tables. So
// the verdict comes from ID_AA64PFR0/PFR1/ISAR2 and anything short of
// provably unaffected resolves to NOT_SUPPORTED.

#include "nova/abi/smccc.h"
#include "nova/arch/cpu_features.hpp"

#include <cstdint>

namespace nova::smccc {

struct Verdict {
  bool          claimed = false;
  std::uint64_t ret     = 0;
};

// The SMCCC Arch service owns 0x8000_0000–0x8000_FFFF.
[[nodiscard]] constexpr auto in_range(std::uint32_t fid) noexcept -> bool {
  return (fid & 0xFFFF0000U) == 0x80000000U;
}

[[nodiscard]] constexpr auto is_implemented(std::uint32_t fid) noexcept -> bool {
  switch (fid) {
  case SMCCC_FN_VERSION:
  case SMCCC_FN_ARCH_FEATURES:
  case SMCCC_FN_WORKAROUND_1:
  case SMCCC_FN_WORKAROUND_2:
  case SMCCC_FN_WORKAROUND_3:
    return true;
  default:
    return false;
  }
}

// The answer for one workaround ID, derived from what the PE reports.
// ARCH_FEATURES and the direct call both route through this so discovery
// can never disagree with the call it describes.
[[nodiscard]] constexpr auto workaround_answer(std::uint32_t fid, const arch::SpeculationState& spec) noexcept
    -> std::uint64_t {
  arch::Mitigation m = arch::Mitigation::kUnknown;
  switch (fid) {
  case SMCCC_FN_WORKAROUND_1:
    m = arch::branch_target_mitigation(spec);
    break;
  case SMCCC_FN_WORKAROUND_2:
    m = arch::store_bypass_mitigation(spec);
    break;
  case SMCCC_FN_WORKAROUND_3:
    m = arch::branch_history_mitigation(spec);
    break;
  default:
    return static_cast<std::uint64_t>(SMCCC_NOT_SUPPORTED);
  }
  return m == arch::Mitigation::kUnaffected ? static_cast<std::uint64_t>(SMCCC_NOT_REQUIRED)
                                            : static_cast<std::uint64_t>(SMCCC_NOT_SUPPORTED);
}

// `arg` is x1 — the queried function ID for ARCH_FEATURES, ignored
// otherwise. `spec` is the boot-decoded ID register view.
[[nodiscard]] constexpr auto dispatch(std::uint32_t fid, std::uint64_t arg, const arch::SpeculationState& spec) noexcept
    -> Verdict {
  if (!in_range(fid)) {
    return {};
  }
  Verdict v{.claimed = true, .ret = 0};

  switch (fid) {
  case SMCCC_FN_VERSION:
    v.ret = SMCCC_VERSION_1_1;
    return v;
  case SMCCC_FN_ARCH_FEATURES: {
    const auto queried = static_cast<std::uint32_t>(arg);
    if (!is_implemented(queried)) {
      v.ret = static_cast<std::uint64_t>(SMCCC_NOT_SUPPORTED);
    } else if (queried == SMCCC_FN_VERSION || queried == SMCCC_FN_ARCH_FEATURES) {
      v.ret = SMCCC_SUCCESS;
    } else {
      v.ret = workaround_answer(queried, spec);
    }
    return v;
  }
  case SMCCC_FN_WORKAROUND_1:
  case SMCCC_FN_WORKAROUND_2:
  case SMCCC_FN_WORKAROUND_3:
    v.ret = workaround_answer(fid, spec);
    return v;
  default:
    v.ret = static_cast<std::uint64_t>(SMCCC_NOT_SUPPORTED);
    return v;
  }
}

} // namespace nova::smccc
