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
// MIDR_EL1), so a guest applies its own EL1-local mitigations from
// what it reads. The hypervisor has no EL2-side sequence to run on the
// guest's behalf, and answering SUCCESS would claim otherwise. What
// the calls must not do is stay undiscoverable: NOT_REQUIRED tells an
// unaffected guest to stop asking, and it is the honest answer wherever
// the hypervisor adds no mitigation of its own.

#include "nova/abi/smccc.h"

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

// `arg` is x1 — the queried function ID for ARCH_FEATURES, ignored
// otherwise.
[[nodiscard]] constexpr auto dispatch(std::uint32_t fid, std::uint64_t arg) noexcept -> Verdict {
  if (!in_range(fid)) {
    return {};
  }
  Verdict v{.claimed = true, .ret = 0};

  switch (fid) {
  case SMCCC_FN_VERSION:
    v.ret = SMCCC_VERSION_1_1;
    return v;
  case SMCCC_FN_ARCH_FEATURES:
    v.ret = is_implemented(static_cast<std::uint32_t>(arg)) ? SMCCC_SUCCESS
                                                            : static_cast<std::uint64_t>(SMCCC_NOT_SUPPORTED);
    return v;
  case SMCCC_FN_WORKAROUND_1:
  case SMCCC_FN_WORKAROUND_2:
  case SMCCC_FN_WORKAROUND_3:
    // Discoverable, and honest: no EL2-side mitigation is applied, and
    // the guest sees the real MIDR to decide its own.
    v.ret = static_cast<std::uint64_t>(SMCCC_NOT_REQUIRED);
    return v;
  default:
    v.ret = static_cast<std::uint64_t>(SMCCC_NOT_SUPPORTED);
    return v;
  }
}

} // namespace nova::smccc
