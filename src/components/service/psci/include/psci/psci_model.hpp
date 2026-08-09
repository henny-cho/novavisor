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
#include "psci/smccc_model.hpp"

#include <array>
#include <cstdint>

namespace nova::psci {

enum class Action : std::uint8_t {
  kNone,         // ret carries the answer
  kSystemOff,    // stop the calling VM
  kSystemReset,  // warm-reset the calling VM
  kCpuOn,        // power on a sibling vCPU (x1=target mpidr, x2=entry, x3=context_id)
  kCpuOff,       // retire the calling vCPU
  kCpuSuspend,   // standby: park the calling vCPU like a trapped WFI (x1=power_state, ignored)
  kAffinityInfo, // report a sibling vCPU's power state (x1=target mpidr)
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

struct Entry {
  std::uint32_t fid    = 0;
  Action        action = Action::kNone; // what the component must carry out
  std::uint64_t ret    = 0;             // x0, where the answer is a constant
};

// Every function this implementation provides, stated once. Discovery
// (PSCI_FEATURES, through is_implemented) and dispatch both read this
// table, so a function cannot be dispatched without being reported, nor
// reported without being dispatched.
inline constexpr std::array kTable{
    Entry{.fid = PSCI_FN_VERSION, .ret = PSCI_VERSION_1_1},
    Entry{.fid = PSCI_FN_CPU_SUSPEND, .action = Action::kCpuSuspend, .ret = PSCI_SUCCESS}, // x0 the caller wakes with
    Entry{.fid = PSCI_FN_CPU_OFF, .action = Action::kCpuOff},
    Entry{.fid = PSCI_FN_CPU_ON, .action = Action::kCpuOn},
    Entry{.fid = PSCI_FN_AFFINITY_INFO, .action = Action::kAffinityInfo},
    Entry{.fid = PSCI_FN_MIGRATE_INFO_TYPE, .ret = PSCI_TOS_NOT_PRESENT_MP}, // no Trusted OS — nothing to migrate
    Entry{.fid = PSCI_FN_SYSTEM_OFF, .action = Action::kSystemOff},
    Entry{.fid = PSCI_FN_SYSTEM_RESET, .action = Action::kSystemReset},
    Entry{.fid = PSCI_FN_FEATURES}, // ret is decided by x1, below
};

// The row for a function ID, or nullptr when the table does not hold
// one. Bit 30 selects the calling convention, so an SMC64 twin resolves
// to the same row as its SMC32 form.
[[nodiscard]] constexpr auto find(std::uint32_t fid) noexcept -> const Entry* {
  const std::uint32_t base = strip_smc64(fid);
  for (const Entry& entry : kTable) {
    if (entry.fid == base) {
      return &entry;
    }
  }
  return nullptr;
}

[[nodiscard]] constexpr auto is_implemented(std::uint32_t fid) noexcept -> bool {
  return find(fid) != nullptr;
}

// A target MPIDR names a vCPU inside the calling VM. The virtual
// topology is flat — Aff0 is the vCPU index, every higher affinity
// field must be zero (matches VMPIDR/GICR_TYPER).
inline constexpr std::uint64_t kInvalidTarget = ~std::uint64_t{0};

[[nodiscard]] constexpr auto target_vcpu(std::uint64_t mpidr) noexcept -> std::uint64_t {
  constexpr std::uint64_t kAff123 = 0xFF00FFFF00ULL; // Aff3[39:32] | Aff2[23:16] | Aff1[15:8]
  return (mpidr & kAff123) != 0 ? kInvalidTarget : (mpidr & 0xFFULL);
}

// `arg` is x1: the queried ID for FEATURES, the target affinity for
// AFFINITY_INFO, ignored otherwise.
[[nodiscard]] constexpr auto dispatch(std::uint32_t fid, std::uint64_t arg) noexcept -> Verdict {
  if (!in_range(fid)) {
    return {};
  }
  const Entry* entry = find(fid);
  if (entry == nullptr) {
    // In range but not ours to implement: still claimed, so the call
    // never reaches another subscriber as an unknown HVC.
    return {.claimed = true, .ret = static_cast<std::uint64_t>(PSCI_NOT_SUPPORTED)};
  }
  if (entry->fid == PSCI_FN_FEATURES) {
    // The queried ID is looked up in the very table dispatch answers
    // from. PSCI_FEATURES also answers for the SMCCC Arch range — guest
    // Linux gates all of SMCCC 1.1 on PSCI_FEATURES(SMCCC_VERSION), and
    // a NOT_SUPPORTED there disables its firmware mitigations entirely.
    const auto queried = static_cast<std::uint32_t>(arg);
    const bool present =
        (in_range(queried) && is_implemented(queried)) || (smccc::in_range(queried) && smccc::is_implemented(queried));
    return {.claimed = true,
            .ret     = present ? std::uint64_t{PSCI_SUCCESS} : static_cast<std::uint64_t>(PSCI_NOT_SUPPORTED)};
  }
  return {.claimed = true, .action = entry->action, .ret = entry->ret};
}

// The range is claimed by its bounds, not by the table: a slot nobody
// implements is still answered here rather than escaping as an unknown
// HVC to another subscriber.
static_assert(
    [] {
      const Verdict hole          = dispatch(0x84000007, 0); // reserved, inside the range
      const auto    not_supported = static_cast<std::uint64_t>(PSCI_NOT_SUPPORTED);
      return in_range(PSCI_FN_VERSION) && in_range(PSCI_FN_CPU_ON | PSCI_FN_SMC64) &&
             !in_range(0x84000020) &&                       // one past the 32 slots the range holds
             !in_range(0x80000000) &&                       // the SMCCC arch service next door
             !dispatch(0x1000, 0).claimed &&                // the demo ABI is somebody else's
             hole.claimed && !is_implemented(0x84000007) && //
             hole.action == Action::kNone &&                //
             hole.ret == not_supported;
    }(),
    "the PSCI range is claimed whole, and a slot the table does not fill answers NOT_SUPPORTED");

// Bit 30 picks the calling convention, never the function — checked
// across the table so a row added later cannot lose its twin. The same
// walk asks PSCI_FEATURES about each row: discovery and dispatch read
// one table, so a function that dispatches must also be reported, in
// either convention.
static_assert(
    [] {
      for (const Entry& entry : kTable) {
        const auto    fid64 = entry.fid | static_cast<std::uint32_t>(PSCI_FN_SMC64);
        const Verdict v32   = dispatch(entry.fid, 0);
        const Verdict v64   = dispatch(fid64, 0);
        if (!v32.claimed || !v64.claimed || v64.action != v32.action || v64.ret != v32.ret) {
          return false;
        }
        if (dispatch(PSCI_FN_FEATURES, entry.fid).ret != std::uint64_t{PSCI_SUCCESS} ||
            dispatch(PSCI_FN_FEATURES, fid64).ret != std::uint64_t{PSCI_SUCCESS}) {
          return false;
        }
      }
      return true;
    }(),
    "every function dispatches identically through its SMC64 twin, and PSCI_FEATURES reports both");

// Only Aff0 names a vCPU; a higher affinity field names something the
// flat virtual topology has no seat for, and must not fold onto seat 0.
static_assert(target_vcpu(0) == 0U && target_vcpu(1) == 1U && target_vcpu(0xFF) == 0xFFU &&
                  target_vcpu(0x100) == kInvalidTarget &&       // Aff1
                  target_vcpu(0x1'0000) == kInvalidTarget &&    // Aff2
                  target_vcpu(0x1'0000'0000) == kInvalidTarget, // Aff3
              "a target MPIDR resolves to a vCPU index only through Aff0");

} // namespace nova::psci
