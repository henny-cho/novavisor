#pragma once

// components/vgic/include/vgic_delivery.hpp
//
// Pure vGICv3 delivery logic — no bare-metal runtime dependency, fully
// host-testable. Multiplexes the per-VCPU pending bitmap (register
// model state, vgic_model.hpp) onto a shadow array of ICH list
// registers: refill() moves deliverable INTIDs into free LRs in
// priority order.
//
// Reference: Arm IHI 0069 (GICv3/v4 Architecture Specification).

#include "components/vgic/include/vgic_model.hpp"

#include <array>
#include <cstddef>
#include <cstdint>

namespace nova::vgic {

inline constexpr std::size_t kMaxLrs = 16; // architectural maximum

// --- ICH_LR<n>_EL2 field encoding (Arm IHI 0069 §9.4.6) --------------------

inline constexpr std::uint64_t kLrStateMask     = 3ULL << 62U; // 00 = inactive
inline constexpr std::uint64_t kLrStatePending  = 1ULL << 62U;
inline constexpr std::uint64_t kLrGroup1        = 1ULL << 60U;
inline constexpr std::uint64_t kLrPriorityShift = 48U;
inline constexpr std::uint64_t kLrVintidMask    = 0xFFFF'FFFFULL;

[[nodiscard]] constexpr auto make_lr(std::uint32_t vintid, std::uint8_t priority) noexcept -> std::uint64_t {
  return kLrStatePending | kLrGroup1 | (static_cast<std::uint64_t>(priority) << kLrPriorityShift) | vintid;
}

// True while the entry is pending or active — the guest has not
// finished consuming it.
[[nodiscard]] constexpr auto lr_in_flight(std::uint64_t lr) noexcept -> bool {
  return (lr & kLrStateMask) != 0U;
}

[[nodiscard]] constexpr auto lr_vintid(std::uint64_t lr) noexcept -> std::uint32_t {
  return static_cast<std::uint32_t>(lr & kLrVintidMask);
}

// --- Per-VCPU state ----------------------------------------------------------

// Full per-VCPU virtual interrupt state: the emulated redistributor
// (register model) plus the LR shadows, which hold the hardware list
// registers while the VCPU is not resident.
struct CpuState {
  RedistState                        redist;
  std::array<std::uint64_t, kMaxLrs> lr{};
};

// --- Delivery -----------------------------------------------------------------

// Pending INTIDs the guest is currently willing to take.
[[nodiscard]] inline auto deliverable(const RedistState& r) noexcept -> std::uint32_t {
  return r.pending & r.isenabler0 & r.igroupr0;
}

[[nodiscard]] inline auto lr_holds(const CpuState& c, std::size_t lr_count, std::uint32_t vintid) noexcept -> bool {
  for (std::size_t i = 0; i < lr_count; ++i) {
    if (lr_in_flight(c.lr[i]) && lr_vintid(c.lr[i]) == vintid) {
      return true;
    }
  }
  return false;
}

// Move deliverable pending INTIDs into free list registers, highest
// priority (lowest value, then lowest INTID) first. An INTID already in
// flight in an LR stays pending — the queued edge is injected once the
// guest consumes the current one. Returns true when deliverable work
// remains undelivered (the caller arms the underflow maintenance IRQ).
inline auto refill(CpuState& c, std::size_t lr_count) noexcept -> bool {
  constexpr std::uint32_t kPriorityLimit = 0x100; // above every 8-bit priority
  for (;;) {
    std::uint32_t       best      = kNumPrivate;
    std::uint32_t       best_prio = kPriorityLimit;
    const std::uint32_t cand      = deliverable(c.redist);
    for (std::uint32_t id = 0; id < kNumPrivate; ++id) {
      if (((cand >> id) & 1U) == 0U || lr_holds(c, lr_count, id)) {
        continue;
      }
      if (c.redist.prio[id] < best_prio) {
        best      = id;
        best_prio = c.redist.prio[id];
      }
    }
    if (best == kNumPrivate) {
      break; // nothing left that is not already in flight
    }

    std::size_t slot = lr_count;
    for (std::size_t i = 0; i < lr_count; ++i) {
      if (!lr_in_flight(c.lr[i])) {
        slot = i;
        break;
      }
    }
    if (slot == lr_count) {
      return true; // all LRs busy — maintenance IRQ refills later
    }
    c.lr[slot] = make_lr(best, c.redist.prio[best]);
    c.redist.pending &= ~(1U << best);
  }
  return deliverable(c.redist) != 0U; // only in-flight duplicates remain
}

} // namespace nova::vgic
