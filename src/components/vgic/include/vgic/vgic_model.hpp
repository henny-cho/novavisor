#pragma once

// components/vgic/include/vgic/vgic_model.hpp
//
// Pure vGICv3 register model — no bare-metal runtime dependency, fully
// host-testable. GICD / GICR frame reads and writes operating on plain
// state structs. Unknown offsets are reported to the caller (the
// component logs them and completes the access RAZ/WI so uncovered
// guest accesses are visible, not fatal). LR injection lives in
// vgic_delivery.hpp.
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
#include <cstdint>

namespace nova::vgic {

inline constexpr std::uint32_t kNumPrivate = 32; // SGI 0..15 + PPI 16..31

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
inline constexpr std::uint32_t kGicrTyperLast  = 1U << 4U;   // highest frame of the VM
inline constexpr std::uint32_t kGicIidrValue   = 0x43B;      // implementer: Arm
inline constexpr std::uint32_t kPidr2GicV3     = 0x3U << 4U; // ArchRev = GICv3

// Which redistributor frame is being emulated: `number` is the vCPU
// index within the VM (GICR_TYPER.Processor_Number AND the Aff0 of the
// affinity word — it must equal the vCPU's VMPIDR so the guest's
// TYPER-affinity walk finds its own frame); `last` terminates that
// walk on the VM's highest frame.
struct RedistId {
  std::uint32_t number = 0;
  bool          last   = true;
};

[[nodiscard]] constexpr auto redist_typer(RedistId id) noexcept -> std::uint64_t {
  return (static_cast<std::uint64_t>(id.number) << 32U) | (static_cast<std::uint64_t>(id.number) << 8U) |
         (id.last ? kGicrTyperLast : 0U);
}

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
// `pending` is the ISPENDR0/ICPENDR0 view; delivery drains it into
// list registers (vgic_delivery.hpp).
struct RedistState {
  bool                                  asleep     = true;
  std::uint32_t                         igroupr0   = ~0U;
  std::uint32_t                         isenabler0 = 0xFFFFU;
  std::uint32_t                         pending    = 0; // software pending, not in any LR
  std::array<std::uint8_t, kNumPrivate> prio{};
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

[[nodiscard]] inline auto redist_read(const RedistState& r, std::uint64_t off, std::uint32_t size,
                                      RedistId id = {}) noexcept -> MmioRead {
  if (off >= kGicrIpriorityr && off + size <= kGicrIpriorityEnd) {
    return {.known = true, .value = detail::prio_read(r.prio, off - kGicrIpriorityr, size)};
  }
  switch (off) {
  case kGicrCtlr:
    return {.known = true, .value = 0};
  case kGicrIidr:
    return {.known = true, .value = kGicIidrValue};
  case kGicrTyper:
    return {.known = true, .value = redist_typer(id)}; // trap layer truncates 4-byte reads
  case kGicrTyperHi:
    return {.known = true, .value = redist_typer(id) >> 32U};
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
    return {.known = true, .value = r.pending};
  case kGicrIcfgr0:
  case kGicrIcfgr1:
    return {.known = true, .value = 0};
  default:
    return {};
  }
}

[[nodiscard]] inline auto redist_write(RedistState& r, std::uint64_t off, std::uint32_t size,
                                       std::uint64_t value) noexcept -> bool {
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
    r.pending |= word;
    return true;
  case kGicrIcpendr0:
    r.pending &= ~word;
    return true;
  case kGicrIcfgr0:
  case kGicrIcfgr1:
    return true; // accepted, ignored
  default:
    return false;
  }
}

} // namespace nova::vgic
