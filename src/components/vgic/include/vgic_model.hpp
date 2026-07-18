#pragma once

// components/vgic/include/vgic_model.hpp
//
// Pure vGICv3 model — no bare-metal runtime dependency, fully
// host-testable. Two concerns live here:
//
//   1. Register emulation: GICD / GICR frame reads and writes operating
//      on plain state structs. Unknown offsets are reported to the
//      caller (the component logs them and completes the access RAZ/WI
//      so uncovered guest accesses are visible, not fatal).
//   2. Delivery: a per-VCPU software pending bitmap for private
//      interrupts (SGI/PPI) multiplexed onto a shadow array of ICH list
//      registers — refill() moves deliverable INTIDs into free LRs in
//      priority order.
//
// Model simplifications (deliberate, documented):
//   - No SPIs: GICD_TYPER advertises 32 INTIDs; SPI registers are
//     unknown-offset RAZ/WI.
//   - GICD_CTLR and GICR_WAKER are stored and read back faithfully but
//     do not gate delivery — the per-INTID enable bits are the single
//     delivery gate.
//   - ICFGR is accepted and ignored (edge/level config has no effect on
//     the LR-injection model).
//
// Reference: Arm IHI 0069 (GICv3/v4 Architecture Specification).

#include "nova/arch/gicv3_regs.h"

#include <array>
#include <cstddef>
#include <cstdint>

namespace nova::vgic {

inline constexpr std::size_t   kMaxLrs     = 16; // architectural maximum
inline constexpr std::uint32_t kNumPrivate = 32; // SGI 0..15 + PPI 16..31

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

// --- Register frame layout ---------------------------------------------------
// Offsets and bits come from the shared architecture header; only the
// values this model chooses to advertise are defined here.

inline constexpr std::uint64_t kGicdFrameSize = NOVA_GICD_FRAME_SIZE;
inline constexpr std::uint64_t kGicrFrameSize = NOVA_GICR_FRAME_SIZE;

// Distributor offsets.
inline constexpr std::uint64_t kGicdCtlr  = NOVA_GICD_CTLR;
inline constexpr std::uint64_t kGicdTyper = NOVA_GICD_TYPER;
inline constexpr std::uint64_t kGicdIidr  = NOVA_GICD_IIDR;
inline constexpr std::uint64_t kGicdPidr2 = NOVA_GICD_PIDR2;

// Redistributor RD_base frame offsets.
inline constexpr std::uint64_t kGicrCtlr    = NOVA_GICR_CTLR;
inline constexpr std::uint64_t kGicrIidr    = NOVA_GICR_IIDR;
inline constexpr std::uint64_t kGicrTyper   = NOVA_GICR_TYPER; // 64-bit
inline constexpr std::uint64_t kGicrTyperHi = NOVA_GICR_TYPER_HI;
inline constexpr std::uint64_t kGicrWaker   = NOVA_GICR_WAKER;
inline constexpr std::uint64_t kGicrPidr2   = NOVA_GICR_PIDR2;

// Redistributor SGI_base frame offsets (RD_base + 64 KiB).
inline constexpr std::uint64_t kGicrSgiFrame     = NOVA_GICR_SGI_FRAME;
inline constexpr std::uint64_t kGicrIgroupr0     = NOVA_GICR_IGROUPR0;
inline constexpr std::uint64_t kGicrIsenabler0   = NOVA_GICR_ISENABLER0;
inline constexpr std::uint64_t kGicrIcenabler0   = NOVA_GICR_ICENABLER0;
inline constexpr std::uint64_t kGicrIspendr0     = NOVA_GICR_ISPENDR0;
inline constexpr std::uint64_t kGicrIcpendr0     = NOVA_GICR_ICPENDR0;
inline constexpr std::uint64_t kGicrIpriorityr   = NOVA_GICR_IPRIORITYR; // 32 bytes
inline constexpr std::uint64_t kGicrIpriorityEnd = kGicrIpriorityr + kNumPrivate;
inline constexpr std::uint64_t kGicrIcfgr0       = NOVA_GICR_ICFGR0;
inline constexpr std::uint64_t kGicrIcfgr1       = NOVA_GICR_ICFGR1;

// Read-only identification values (emulation policy, not architecture).
inline constexpr std::uint32_t kGicdTyperValue = 0;          // ITLinesNumber=0: no SPIs
inline constexpr std::uint32_t kGicrTyperLast  = 1U << 4U;   // sole redistributor
inline constexpr std::uint32_t kGicIidrValue   = 0x43B;      // implementer: Arm
inline constexpr std::uint32_t kPidr2GicV3     = 0x3U << 4U; // ArchRev = GICv3

// GICR_WAKER bits.
inline constexpr std::uint32_t kWakerProcessorSleep = NOVA_GICR_WAKER_PROCESSOR_SLEEP;
inline constexpr std::uint32_t kWakerChildrenAsleep = NOVA_GICR_WAKER_CHILDREN_ASLEEP;

// --- State ------------------------------------------------------------------

struct DistState {
  std::uint32_t ctlr = 0;
};

// Reset state: all private interrupts in Group 1 and SGIs permanently
// enabled (GICv3 allows SGI enables to be RAO/WI) — hypervisor-injected
// doorbell SGIs work before the guest ever touches its redistributor.
// PPIs start disabled: guests enable them through ISENABLER0.
struct RedistState {
  bool                                  asleep     = true;
  std::uint32_t                         igroupr0   = ~0U;
  std::uint32_t                         isenabler0 = 0xFFFFU;
  std::array<std::uint8_t, kNumPrivate> prio{};
};

// Full per-VCPU virtual interrupt state. `lr` shadows the hardware list
// registers while the VCPU is not resident.
struct CpuState {
  RedistState                        redist;
  std::uint32_t                      pending = 0; // software pending, not in any LR
  std::array<std::uint64_t, kMaxLrs> lr{};
};

struct MmioRead {
  bool          known = false;
  std::uint64_t value = 0;
};

// --- Register emulation -------------------------------------------------------

namespace detail {

inline constexpr std::uint32_t kBitsPerByte = 8;

// Byte-lane read/write helpers for the byte-indexed IPRIORITYR block.
inline auto prio_read(const std::array<std::uint8_t, kNumPrivate>& prio, std::uint64_t first,
                      std::uint32_t size) noexcept -> std::uint64_t {
  std::uint64_t v = 0;
  for (std::uint32_t i = 0; i < size; ++i) {
    v |= static_cast<std::uint64_t>(prio[first + i]) << (kBitsPerByte * i);
  }
  return v;
}

inline void prio_write(std::array<std::uint8_t, kNumPrivate>& prio, std::uint64_t first, std::uint32_t size,
                       std::uint64_t value) noexcept {
  for (std::uint32_t i = 0; i < size; ++i) {
    prio[first + i] = static_cast<std::uint8_t>(value >> (kBitsPerByte * i));
  }
}

} // namespace detail

[[nodiscard]] inline auto dist_read(const DistState& d, std::uint64_t off, std::uint32_t /*size*/) noexcept
    -> MmioRead {
  switch (off) {
  case kGicdCtlr:
    return {.known = true, .value = d.ctlr};
  case kGicdTyper:
    return {.known = true, .value = kGicdTyperValue};
  case kGicdIidr:
    return {.known = true, .value = kGicIidrValue};
  case kGicdPidr2:
    return {.known = true, .value = kPidr2GicV3};
  default:
    return {};
  }
}

[[nodiscard]] inline auto dist_write(DistState& d, std::uint64_t off, std::uint32_t /*size*/,
                                     std::uint64_t value) noexcept -> bool {
  if (off == kGicdCtlr) {
    d.ctlr = static_cast<std::uint32_t>(value);
    return true;
  }
  return false;
}

[[nodiscard]] inline auto redist_read(const CpuState& c, std::uint64_t off, std::uint32_t size) noexcept -> MmioRead {
  const RedistState& r = c.redist;
  if (off >= kGicrIpriorityr && off + size <= kGicrIpriorityEnd) {
    return {.known = true, .value = detail::prio_read(r.prio, off - kGicrIpriorityr, size)};
  }
  switch (off) {
  case kGicrCtlr:
    return {.known = true, .value = 0};
  case kGicrIidr:
    return {.known = true, .value = kGicIidrValue};
  case kGicrTyper:
    return {.known = true, .value = kGicrTyperLast}; // upper word 0 for size 8 too
  case kGicrTyperHi:
    return {.known = true, .value = 0};
  case kGicrWaker:
    // ChildrenAsleep mirrors ProcessorSleep — the wake handshake
    // completes immediately (there is no physical child to wait for).
    return {.known = true, .value = r.asleep ? (kWakerProcessorSleep | kWakerChildrenAsleep) : 0U};
  case kGicrPidr2:
    return {.known = true, .value = kPidr2GicV3};
  case kGicrIgroupr0:
    return {.known = true, .value = r.igroupr0};
  case kGicrIsenabler0:
  case kGicrIcenabler0:
    return {.known = true, .value = r.isenabler0};
  case kGicrIspendr0:
  case kGicrIcpendr0:
    return {.known = true, .value = c.pending};
  case kGicrIcfgr0:
  case kGicrIcfgr1:
    return {.known = true, .value = 0};
  default:
    return {};
  }
}

[[nodiscard]] inline auto redist_write(CpuState& c, std::uint64_t off, std::uint32_t size, std::uint64_t value) noexcept
    -> bool {
  RedistState& r = c.redist;
  if (off >= kGicrIpriorityr && off + size <= kGicrIpriorityEnd) {
    detail::prio_write(r.prio, off - kGicrIpriorityr, size, value);
    return true;
  }
  const auto word = static_cast<std::uint32_t>(value);
  switch (off) {
  case kGicrCtlr:
    return true; // no LPI support — WI
  case kGicrWaker:
    r.asleep = (word & kWakerProcessorSleep) != 0U;
    return true;
  case kGicrIgroupr0:
    r.igroupr0 = word;
    return true;
  case kGicrIsenabler0:
    r.isenabler0 |= word; // write-1-to-set
    return true;
  case kGicrIcenabler0:
    r.isenabler0 &= ~word; // write-1-to-clear
    return true;
  case kGicrIspendr0:
    c.pending |= word;
    return true;
  case kGicrIcpendr0:
    c.pending &= ~word;
    return true;
  case kGicrIcfgr0:
  case kGicrIcfgr1:
    return true; // accepted, ignored
  default:
    return false;
  }
}

// --- Delivery -----------------------------------------------------------------

// Pending INTIDs the guest is currently willing to take.
[[nodiscard]] inline auto deliverable(const CpuState& c) noexcept -> std::uint32_t {
  return c.pending & c.redist.isenabler0 & c.redist.igroupr0;
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
    const std::uint32_t cand      = deliverable(c);
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
    c.pending &= ~(1U << best);
  }
  return deliverable(c) != 0U; // only in-flight duplicates remain
}

} // namespace nova::vgic
