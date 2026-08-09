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

#include <array>
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

struct Entry {
  std::uint32_t fid = 0;
  // Set when the answer is a claim about the hardware: the query reads
  // what the PE discloses, and only a disclosed property earns
  // NOT_REQUIRED.
  arch::Mitigation (*query)(const arch::SpeculationState&) noexcept = nullptr;
  std::uint64_t ret                                                 = 0; // x0, where no query decides it
};

// Every function this service provides, stated once — including which
// ID register verdict answers each workaround. Discovery (ARCH_FEATURES
// here, PSCI_FEATURES through is_implemented) and the call itself both
// read this table, so discovery can never promise what the call refuses.
inline constexpr std::array kTable{
    Entry{.fid = SMCCC_FN_VERSION, .ret = SMCCC_VERSION_1_1},
    Entry{.fid = SMCCC_FN_ARCH_FEATURES}, // ret is decided by x1, below
    Entry{.fid = SMCCC_FN_WORKAROUND_1, .query = arch::branch_target_mitigation},
    Entry{.fid = SMCCC_FN_WORKAROUND_2, .query = arch::store_bypass_mitigation},
    Entry{.fid = SMCCC_FN_WORKAROUND_3, .query = arch::branch_history_mitigation},
};

// The row for a function ID, or nullptr when the table does not hold
// one. Exact match: the Arch service has no SMC64 twins.
[[nodiscard]] constexpr auto find(std::uint32_t fid) noexcept -> const Entry* {
  for (const Entry& entry : kTable) {
    if (entry.fid == fid) {
      return &entry;
    }
  }
  return nullptr;
}

[[nodiscard]] constexpr auto is_implemented(std::uint32_t fid) noexcept -> bool {
  return find(fid) != nullptr;
}

// What one row answers on this PE: a workaround states what the ID
// registers support, everything else is plainly present.
[[nodiscard]] constexpr auto entry_answer(const Entry& entry, const arch::SpeculationState& spec) noexcept
    -> std::uint64_t {
  if (entry.query == nullptr) {
    return static_cast<std::uint64_t>(SMCCC_SUCCESS);
  }
  return entry.query(spec) == arch::Mitigation::kUnaffected ? static_cast<std::uint64_t>(SMCCC_NOT_REQUIRED)
                                                            : static_cast<std::uint64_t>(SMCCC_NOT_SUPPORTED);
}

// What discovery reports for one function ID. ARCH_FEATURES routes a
// workaround ID through the same row the call answers from, so the two
// can never disagree.
[[nodiscard]] constexpr auto feature_answer(std::uint32_t fid, const arch::SpeculationState& spec) noexcept
    -> std::uint64_t {
  const Entry* entry = find(fid);
  return entry == nullptr ? static_cast<std::uint64_t>(SMCCC_NOT_SUPPORTED) : entry_answer(*entry, spec);
}

// `arg` is x1 — the queried function ID for ARCH_FEATURES, ignored
// otherwise. `spec` is the boot-decoded ID register view.
[[nodiscard]] constexpr auto dispatch(std::uint32_t fid, std::uint64_t arg, const arch::SpeculationState& spec) noexcept
    -> Verdict {
  if (!in_range(fid)) {
    return {};
  }
  const Entry* entry = find(fid);
  if (entry == nullptr) {
    // In range but not ours to implement: still claimed, so the call
    // never reaches another subscriber as an unknown HVC.
    return {.claimed = true, .ret = static_cast<std::uint64_t>(SMCCC_NOT_SUPPORTED)};
  }
  if (entry->fid == SMCCC_FN_ARCH_FEATURES) {
    return {.claimed = true, .ret = feature_answer(static_cast<std::uint32_t>(arg), spec)};
  }
  return {.claimed = true, .ret = entry->query != nullptr ? entry_answer(*entry, spec) : entry->ret};
}

} // namespace nova::smccc
