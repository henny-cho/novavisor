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
#include <bit>
#include <cstddef>
#include <cstdint>
#include <span>

namespace nova::vgic {

inline constexpr std::size_t kMaxLrs = 16; // architectural maximum

// --- ICH_LR<n>_EL2 field encoding (Arm IHI 0069 §9.4.6) --------------------

inline constexpr std::uint64_t kLrStateMask     = 3ULL << 62U; // 00 = inactive
inline constexpr std::uint64_t kLrStatePending  = 1ULL << 62U;
inline constexpr std::uint64_t kLrStateActive   = 2ULL << 62U; // guest acknowledged, not yet deactivated
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

// ISPENDR-word mask of SPIs holding a live EoI token: these must
// survive a guest ICPENDR1 clear (the token still owes a device EOI).
[[nodiscard]] constexpr auto pending_token_mask(std::span<const EoiToken> spi_tokens) noexcept -> std::uint32_t {
  std::uint32_t mask = 0;
  for (std::size_t i = 0; i < spi_tokens.size(); ++i) {
    mask |= (spi_tokens[i].valid() ? 1U : 0U) << i;
  }
  return mask;
}

// Harvest EoI'd list registers from the shadow after the caller synced
// hardware into it: EISR bit i marks LR i as invalid-with-EOI. Consumes
// the slot's token (valid ones are emitted) and zeroes the shadow LR.
// `cleared` reports which slots were emptied so the glue can zero the
// hardware copies.
struct EoiHarvest {
  std::array<EoiToken, kMaxLrs> tokens{};
  std::size_t                   count   = 0;
  std::uint64_t                 cleared = 0;
};

[[nodiscard]] constexpr auto harvest_eois(CpuState& cpu, std::uint64_t eisr, std::size_t lr_count) noexcept
    -> EoiHarvest {
  EoiHarvest harvest{};
  for (std::size_t i = 0; i < lr_count && i < cpu.lr.size(); ++i) {
    if ((eisr & (1ULL << i)) == 0U) {
      continue;
    }
    const EoiToken token = take_eoi_token(cpu, i);
    if (token.valid()) {
      harvest.tokens[harvest.count++] = token;
    }
    cpu.lr[i] = 0;
    harvest.cleared |= 1ULL << i;
  }
  return harvest;
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

// Move deliverable pending INTIDs into free list registers, highest
// priority (lowest value, then lowest INTID) first. An INTID already in
// flight in an LR cannot be injected twice, so it stays pending until
// the guest retires the copy it has. It must not arm underflow
// maintenance by itself: QEMU reports an immediate underflow while free
// LRs exist, creating an IRQ storm. With a distributor bank, the vCPU's
// routed SPI set joins the private candidates. Returns true only when a
// distinct deliverable INTID could not fit because every LR is occupied.
//
// Every INTID left pending that way gets EOI maintenance armed on the LR
// holding it, so the guest's deactivate re-enters EL2 and this refill
// runs again. Nothing else guarantees that retry: a compute-bound vCPU
// never reaches the wfi that would re-derive delivery, and the virtual
// timer is masked at injection time — its dropped copy would leave the
// guest with no re-arm, no further assertion, and no timekeeping at all.
inline auto refill(CpuState& c, std::size_t lr_count, DistState* dist = nullptr, std::uint32_t vcpu = 0,
                   std::size_t vcpus = 1, std::array<EoiToken, kNumSpis>* spi_tokens = nullptr) noexcept -> bool {
  constexpr std::uint32_t kPriorityLimit = 0x100; // above every 8-bit priority

  // The deliverable and in-flight sets are invariant across iterations
  // except for the bits this loop itself consumes — compute both once
  // (route resolution walks every SPI, the in-flight scan every LR) and
  // track consumption locally instead of re-deriving them per INTID.
  std::uint64_t inflight = 0;
  for (std::size_t i = 0; i < lr_count; ++i) {
    if (lr_in_flight(c.lr[i])) {
      const std::uint32_t id = lr_vintid(c.lr[i]);
      if (id < kMaxIntid) {
        inflight |= 1ULL << id;
      }
    }
  }
  const std::uint32_t priv       = deliverable(c.redist);
  const std::uint32_t spis       = dist != nullptr ? spi_deliverable(*dist, vcpu, vcpus) : 0U;
  const std::uint64_t wanted     = (static_cast<std::uint64_t>(spis) << kNumPrivate) | priv;
  std::uint64_t       candidates = wanted & ~inflight;

  if ((wanted & inflight) != 0U) {
    for (std::size_t i = 0; i < lr_count; ++i) {
      const std::uint32_t id = lr_vintid(c.lr[i]);
      if (lr_in_flight(c.lr[i]) && id < kMaxIntid && (wanted & (1ULL << id)) != 0U) {
        c.lr[i] |= kLrEoi;
      }
    }
  }

  for (;;) {
    std::uint32_t best      = kMaxIntid;
    std::uint32_t best_prio = kPriorityLimit;
    for (std::uint64_t bits = candidates; bits != 0U; bits &= bits - 1U) {
      const auto         id   = static_cast<std::uint32_t>(std::countr_zero(bits));
      const std::uint8_t prio = id >= kNumPrivate ? dist->spi_prio[id - kNumPrivate] : c.redist.prio[id];
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
    candidates &= ~(1ULL << best);
    if (best < kNumPrivate) {
      c.redist.pending &= ~(1U << best);
    } else {
      dist->spi_pending &= ~(1U << (best - kNumPrivate));
    }
  }
  return false; // no distinct candidate remains; duplicates wait for a later refill
}

} // namespace nova::vgic
