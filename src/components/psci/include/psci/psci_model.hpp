#pragma once

// components/psci/include/psci/psci_model.hpp
//
// Pure PSCI dispatch, host-testable. Maps an SMCCC function ID (and
// its first argument) to a verdict: whether the PSCI range claims the
// call, which power action the component must perform, and the value
// returned in x0. The component (psci.cpp) only wires actions to the
// core_vcpu lifecycle API.
//
// The whole PSCI range (SMC32 and SMC64, 0x…000–0x…01F) is claimed —
// we ARE the guest's PSCI implementation, so unimplemented functions
// answer NOT_SUPPORTED instead of leaking "unknown HVC" warnings.

#include "nova/abi/psci.h"

#include <cstdint>

namespace nova::psci {

enum class Action : std::uint8_t {
  kNone,        // ret carries the answer
  kSystemOff,   // stop the calling VM
  kSystemReset, // warm-reset the calling VM
};

struct Verdict {
  bool          claimed = false;
  Action        action  = Action::kNone;
  std::uint64_t ret     = 0;
};

// True for the 32 standard PSCI slots in either calling convention.
[[nodiscard]] constexpr auto in_range(std::uint32_t fid) noexcept -> bool {
  return (fid & ~(static_cast<std::uint32_t>(PSCI_FN_SMC64) | 0x1FU)) == PSCI_FN_VERSION;
}

// SMC64 twins are accepted alongside SMC32 — same semantics here (all
// implemented arguments fit 32 bits).
[[nodiscard]] constexpr auto strip_smc64(std::uint32_t fid) noexcept -> std::uint32_t {
  return fid & ~static_cast<std::uint32_t>(PSCI_FN_SMC64);
}

[[nodiscard]] constexpr auto is_implemented(std::uint32_t fid) noexcept -> bool {
  switch (strip_smc64(fid)) {
  case PSCI_FN_VERSION:
  case PSCI_FN_AFFINITY_INFO:
  case PSCI_FN_SYSTEM_OFF:
  case PSCI_FN_SYSTEM_RESET:
  case PSCI_FN_FEATURES:
    return true;
  default:
    return false; // CPU_ON/CPU_OFF arrive with multi-vCPU guests
  }
}

// `arg` is x1: the queried ID for FEATURES, the target affinity for
// AFFINITY_INFO, ignored otherwise.
[[nodiscard]] constexpr auto dispatch(std::uint32_t fid, std::uint64_t arg) noexcept -> Verdict {
  if (!in_range(fid)) {
    return {};
  }
  Verdict v{.claimed = true, .action = Action::kNone, .ret = 0};

  switch (strip_smc64(fid)) {
  case PSCI_FN_VERSION:
    v.ret = PSCI_VERSION_1_1;
    return v;
  case PSCI_FN_FEATURES:
    v.ret = (in_range(static_cast<std::uint32_t>(arg)) && is_implemented(static_cast<std::uint32_t>(arg)))
                ? PSCI_SUCCESS
                : static_cast<std::uint64_t>(PSCI_NOT_SUPPORTED);
    return v;
  case PSCI_FN_AFFINITY_INFO:
    // One vCPU per VM: only affinity 0 exists, and a caller asking is
    // by definition running on it.
    v.ret = (arg == 0) ? PSCI_AFFINITY_ON : static_cast<std::uint64_t>(PSCI_INVALID_PARAMETERS);
    return v;
  case PSCI_FN_SYSTEM_OFF:
    v.action = Action::kSystemOff;
    return v;
  case PSCI_FN_SYSTEM_RESET:
    v.action = Action::kSystemReset;
    return v;
  default:
    v.ret = static_cast<std::uint64_t>(PSCI_NOT_SUPPORTED);
    return v;
  }
}

} // namespace nova::psci
