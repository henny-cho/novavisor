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
//   - One SPI word: GICD_TYPER advertises 64 INTIDs (32 private + 32
//     SPIs); higher SPI banks are unknown-offset RAZ/WI.
//   - GICD_CTLR and GICR_WAKER are stored and read back faithfully but
//     do not gate delivery — the per-INTID enable bits are the single
//     delivery gate.
//   - ICFGR is accepted and ignored (edge/level config has no effect on
//     the LR-injection model).
//   - IROUTER keeps Aff0 only (flat virtual topology, VMPIDR Aff0 =
//     vCPU index); IRM (1-of-N) is not honored. A pending SPI is not
//     re-routed by a later IROUTER write — the new route applies from
//     the next post.
//
// Reference: Arm IHI 0069 (GICv3/v4 Architecture Specification).

#include "nova/arch/gicv3_regs.h"

#include <array>
#include <cstdint>

namespace nova::vgic {

inline constexpr std::uint32_t kNumPrivate = 32; // SGI 0..15 + PPI 16..31
inline constexpr std::uint32_t kNumSpis    = 32; // SPI 32..63 (one register word)
inline constexpr std::uint32_t kMaxIntid   = kNumPrivate + kNumSpis;

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

// Distributor SPI banks (word 1 = INTIDs 32..63).
inline constexpr std::uint64_t kGicdIgroupr1      = NOVA_GICD_IGROUPR1;
inline constexpr std::uint64_t kGicdIsenabler1    = NOVA_GICD_ISENABLER1;
inline constexpr std::uint64_t kGicdIcenabler1    = NOVA_GICD_ICENABLER1;
inline constexpr std::uint64_t kGicdIspendr1      = NOVA_GICD_ISPENDR1;
inline constexpr std::uint64_t kGicdIcpendr1      = NOVA_GICD_ICPENDR1;
inline constexpr std::uint64_t kGicdIpriorityrSpi = NOVA_GICD_IPRIORITYR + kNumPrivate;
inline constexpr std::uint64_t kGicdIpriorityrEnd = NOVA_GICD_IPRIORITYR + kMaxIntid;
inline constexpr std::uint64_t kGicdIcfgr2        = NOVA_GICD_ICFGR2;
inline constexpr std::uint64_t kGicdIrouterSpi    = NOVA_GICD_IROUTER + 8ULL * kNumPrivate;
inline constexpr std::uint64_t kGicdIrouterEnd    = NOVA_GICD_IROUTER + 8ULL * kMaxIntid;

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
inline constexpr std::uint32_t kGicdTyperValue = 1;          // ITLinesNumber=1: INTIDs 0..63
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

// --- ICC_SGI1R_EL1 decode -----------------------------------------------------
// A trapped Group 1 SGI write (ICH_HCR_EL2.TC), routed by the smp
// component. Field layout (Arm IHI 0069): TargetList[15:0], Aff1[23:16],
// INTID[27:24], Aff2[39:32], IRM[40], RS[47:44], Aff3[55:48].

inline constexpr std::uint64_t kSgi1rTargetMask = 0xFFFFULL;
inline constexpr std::uint64_t kSgi1rIrm        = 1ULL << 40U;
inline constexpr std::uint64_t kSgi1rRsMask     = 0xFULL << 44U;

[[nodiscard]] constexpr auto sgi1r_intid(std::uint64_t v) noexcept -> std::uint32_t {
  return static_cast<std::uint32_t>((v >> 24U) & 0xFU);
}

// The set of target vCPU indices (bitmask) within the sender's VM.
// IRM broadcasts to every sibling but the sender; otherwise the flat
// virtual topology (all upper affinities zero, VMPIDR Aff0 = vCPU
// index) means Aff3/Aff2/Aff1 must be zero and RS must be zero
// (RangeSelector blocks past the first need 16+ vCPUs). Self-targeting
// through TargetList is architecturally allowed and kept.
[[nodiscard]] constexpr auto sgi1r_targets(std::uint64_t v, std::size_t sender, std::size_t vcpus) noexcept
    -> std::uint32_t {
  const auto all = static_cast<std::uint32_t>((1U << vcpus) - 1U);
  if ((v & kSgi1rIrm) != 0U) {
    return all & ~(1U << sender);
  }
  constexpr std::uint64_t kAff123 = (0xFFULL << 48U) | (0xFFULL << 32U) | (0xFFULL << 16U);
  if ((v & (kAff123 | kSgi1rRsMask)) != 0U) {
    return 0;
  }
  return static_cast<std::uint32_t>(v & kSgi1rTargetMask) & all;
}

// --- State ------------------------------------------------------------------

// Distributor state — one per VM. The SPI banks (INTIDs 32..63) are
// distributor-global: enable/pending/priority/route are shared by all
// the VM's vCPUs and IROUTER Aff0 picks the delivery target. Reset
// state: SPIs in Group 1 (like the private word), disabled, routed to
// vCPU 0. `spi_pending` mirrors `RedistState::pending` — software
// pending not yet in any LR.
struct DistState {
  std::uint32_t                      ctlr        = 0;
  std::uint32_t                      spi_group   = ~0U;
  std::uint32_t                      spi_enabled = 0;
  std::uint32_t                      spi_pending = 0;
  std::array<std::uint8_t, kNumSpis> spi_prio{};
  std::array<std::uint8_t, kNumSpis> spi_route{};
};

// Delivery target of an SPI within its VM: IROUTER Aff0 clamped to the
// VM's vCPU count (an out-of-range route falls back to vCPU 0).
[[nodiscard]] constexpr auto spi_target(const DistState& d, std::uint32_t intid, std::size_t vcpus) noexcept
    -> std::uint32_t {
  const std::uint32_t route = d.spi_route[intid - kNumPrivate];
  return route < vcpus ? route : 0U;
}

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

[[nodiscard]] inline auto dist_read(const DistState& d, std::uint64_t off, std::uint32_t size) noexcept -> MmioRead {
  if (off >= kGicdIpriorityrSpi && off + size <= kGicdIpriorityrEnd) {
    return {.known = true, .value = detail::prio_read(d.spi_prio, off - kGicdIpriorityrSpi, size)};
  }
  if (off >= kGicdIrouterSpi && off < kGicdIrouterEnd) {
    // Aligned low word (or a full 64-bit read) sees the stored Aff0;
    // the high word is always zero in the flat virtual topology.
    return {.known = true, .value = (off % 8U) == 0U ? d.spi_route[(off - kGicdIrouterSpi) / 8U] : 0U};
  }
  switch (off) {
  case kGicdCtlr:
    return {.known = true, .value = d.ctlr};
  case kGicdTyper:
    return {.known = true, .value = kGicdTyperValue};
  case kGicdIidr:
    return {.known = true, .value = kGicIidrValue};
  case kGicdPidr2:
    return {.known = true, .value = kPidr2GicV3};
  case kGicdIgroupr1:
    return {.known = true, .value = d.spi_group};
  case kGicdIsenabler1:
  case kGicdIcenabler1:
    return {.known = true, .value = d.spi_enabled};
  case kGicdIspendr1:
  case kGicdIcpendr1:
    return {.known = true, .value = d.spi_pending};
  case kGicdIcfgr2:
    return {.known = true, .value = 0};
  default:
    return {};
  }
}

[[nodiscard]] inline auto dist_write(DistState& d, std::uint64_t off, std::uint32_t size, std::uint64_t value) noexcept
    -> bool {
  if (off >= kGicdIpriorityrSpi && off + size <= kGicdIpriorityrEnd) {
    detail::prio_write(d.spi_prio, off - kGicdIpriorityrSpi, size, value);
    return true;
  }
  if (off >= kGicdIrouterSpi && off < kGicdIrouterEnd) {
    if ((off % 8U) == 0U) {
      d.spi_route[(off - kGicdIrouterSpi) / 8U] = static_cast<std::uint8_t>(value); // Aff0 only, IRM ignored
    }
    return true;
  }
  const auto word = static_cast<std::uint32_t>(value);
  switch (off) {
  case kGicdCtlr:
    d.ctlr = word;
    return true;
  case kGicdIgroupr1:
    d.spi_group = word;
    return true;
  case kGicdIsenabler1:
    d.spi_enabled |= word; // write-1-to-set
    return true;
  case kGicdIcenabler1:
    d.spi_enabled &= ~word; // write-1-to-clear
    return true;
  case kGicdIspendr1:
    d.spi_pending |= word;
    return true;
  case kGicdIcpendr1:
    d.spi_pending &= ~word;
    return true;
  case kGicdIcfgr2:
    return true; // accepted, ignored (level assumed)
  default:
    return false;
  }
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
