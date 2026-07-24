#pragma once

// components/vgic/include/vgic/vgic_delivery.hpp
//
// Pure vGICv3 delivery logic — no bare-metal runtime dependency, fully
// host-testable. Multiplexes the per-VCPU pending bitmap (register
// model state, vgic_model.hpp) onto a shadow array of ICH list
// registers: refill() moves deliverable INTIDs into free LRs in
// priority order.
//
// Reference: Arm IHI 0069 (GICv3/v4 Architecture Specification).

#include "vgic/vgic_model.hpp"

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
inline constexpr std::uint64_t kLrEoi           = 1ULL << 41U;
inline constexpr std::uint64_t kLrVintidMask    = 0xFFFF'FFFFULL;

[[nodiscard]] constexpr auto make_lr(std::uint32_t vintid, std::uint8_t priority, bool maintenance_eoi = false) noexcept
    -> std::uint64_t {
  return kLrStatePending | kLrGroup1 | (static_cast<std::uint64_t>(priority) << kLrPriorityShift) |
         (maintenance_eoi ? kLrEoi : 0U) | vintid;
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
  struct EoiToken {
    std::uint32_t virtual_intid  = 0;
    std::uint32_t physical_intid = 0;
    std::uint64_t generation     = 0;

    [[nodiscard]] constexpr auto valid() const noexcept -> bool { return generation != 0U; }
  };
  std::array<EoiToken, kMaxLrs> lr_token{};
};

using EoiToken = CpuState::EoiToken;

[[nodiscard]] constexpr auto take_eoi_token(CpuState& cpu, std::size_t slot) noexcept -> EoiToken {
  if (slot >= cpu.lr_token.size()) {
    return {};
  }
  const EoiToken token = cpu.lr_token[slot];
  cpu.lr_token[slot]   = {};
  return token;
}

// --- Delivery -----------------------------------------------------------------

// Pending INTIDs the guest is currently willing to take. The enable
// bit is the single delivery gate — group configuration is stored for
// read-back only. Every LR is injected as Group 1 (make_lr) no matter
// how IGROUPR programs the INTID: a secure-convention guest (Zephyr
// writes IGROUPR0 = 0) would otherwise never receive anything, while
// its ICC_IGRPEN1 enable takes the Group 1 delivery just fine under
// DS = 1.
[[nodiscard]] inline auto deliverable(const RedistState& r) noexcept -> std::uint32_t {
  return r.pending & r.isenabler0;
}

// Pending SPIs (bit i = INTID 32+i) this vCPU may take: gated by the
// per-VM enable bank and routed here by IROUTER (spi_target's clamp
// keeps out-of-range routes on vCPU 0).
[[nodiscard]] inline auto spi_deliverable(const DistState& d, std::uint32_t vcpu, std::size_t vcpus) noexcept
    -> std::uint32_t {
  std::uint32_t routed = 0;
  for (std::uint32_t i = 0; i < kNumSpis; ++i) {
    routed |= (spi_target(d, kNumPrivate + i, vcpus) == vcpu ? 1U : 0U) << i;
  }
  return d.spi_pending & d.spi_enabled & routed;
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
// flight in an LR stays pending and is reconsidered on the guest's next
// WFI. It must not arm underflow maintenance by itself: QEMU reports an
// immediate underflow while free LRs exist, creating an IRQ storm. With
// a distributor bank, the vCPU's routed SPI set joins the private
// candidates. Returns true only when a distinct deliverable INTID could
// not fit because every LR is occupied.
inline auto refill(CpuState& c, std::size_t lr_count, DistState* dist = nullptr, std::uint32_t vcpu = 0,
                   std::size_t vcpus = 1, std::array<EoiToken, kNumSpis>* spi_tokens = nullptr) noexcept -> bool {
  constexpr std::uint32_t kPriorityLimit = 0x100; // above every 8-bit priority
  for (;;) {
    std::uint32_t       best      = kMaxIntid;
    std::uint32_t       best_prio = kPriorityLimit;
    const std::uint32_t priv      = deliverable(c.redist);
    const std::uint32_t spis      = dist != nullptr ? spi_deliverable(*dist, vcpu, vcpus) : 0U;
    for (std::uint32_t id = 0; id < kMaxIntid; ++id) {
      const bool spi  = id >= kNumPrivate;
      const bool cand = ((spi ? spis >> (id - kNumPrivate) : priv >> id) & 1U) != 0U;
      if (!cand || lr_holds(c, lr_count, id)) {
        continue;
      }
      const std::uint8_t prio = spi ? dist->spi_prio[id - kNumPrivate] : c.redist.prio[id];
      if (prio < best_prio) {
        best      = id;
        best_prio = prio;
      }
    }
    if (best == kMaxIntid) {
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
    EoiToken token{};
    if (best >= kNumPrivate && spi_tokens != nullptr) {
      token                             = (*spi_tokens)[best - kNumPrivate];
      (*spi_tokens)[best - kNumPrivate] = {};
    }
    c.lr_token[slot] = token;
    c.lr[slot]       = make_lr(best, static_cast<std::uint8_t>(best_prio), token.valid());
    if (best < kNumPrivate) {
      c.redist.pending &= ~(1U << best);
    } else {
      dist->spi_pending &= ~(1U << (best - kNumPrivate));
    }
  }
  return false; // no distinct candidate remains; duplicates wait for a later refill
}

} // namespace nova::vgic
