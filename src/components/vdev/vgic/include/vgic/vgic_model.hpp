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
//   - IGROUPR is stored for read-back but does not gate delivery, and
//     every LR is injected as Group 1: with DS = 1 a secure-convention
//     guest programs Group 0 (Zephyr writes IGROUPR0 = 0) yet enables
//     ICC_IGRPEN1 — honoring the group bits would silently drop its
//     entire interrupt delivery.
//   - IROUTER keeps Aff0 only (flat virtual topology, VMPIDR Aff0 =
//     vCPU index); IRM (1-of-N) is not honored. A pending SPI is not
//     re-routed by a later IROUTER write — the new route applies from
//     the next post.
//
// Reference: Arm IHI 0069 (GICv3/v4 Architecture Specification).

#include "nova/arch/gicv3/regs.h"

#include <array>
#include <cstddef>
#include <cstdint>
#include <span>

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
inline constexpr std::uint64_t kGicdCtlr   = NOVA_GICD_CTLR;
inline constexpr std::uint64_t kGicdTyper  = NOVA_GICD_TYPER;
inline constexpr std::uint64_t kGicdIidr   = NOVA_GICD_IIDR;
inline constexpr std::uint64_t kGicdTyper2 = NOVA_GICD_TYPER2;
inline constexpr std::uint64_t kGicdPidr2  = NOVA_GICD_PIDR2;

// Distributor SPI banks (word 1 = INTIDs 32..63).
inline constexpr std::uint64_t kGicdIgroupr1      = NOVA_GICD_IGROUPR1;
inline constexpr std::uint64_t kGicdIsenabler1    = NOVA_GICD_ISENABLER1;
inline constexpr std::uint64_t kGicdIcenabler1    = NOVA_GICD_ICENABLER1;
inline constexpr std::uint64_t kGicdIspendr1      = NOVA_GICD_ISPENDR1;
inline constexpr std::uint64_t kGicdIcpendr1      = NOVA_GICD_ICPENDR1;
inline constexpr std::uint64_t kGicdIsactiver1    = NOVA_GICD_ISACTIVER1;
inline constexpr std::uint64_t kGicdIcactiver1    = NOVA_GICD_ICACTIVER1;
inline constexpr std::uint64_t kGicdIpriorityrSpi = NOVA_GICD_IPRIORITYR + kNumPrivate;
inline constexpr std::uint64_t kGicdIpriorityrEnd = NOVA_GICD_IPRIORITYR + kMaxIntid;
inline constexpr std::uint64_t kGicdIcfgr2        = NOVA_GICD_ICFGR2;
inline constexpr std::uint64_t kGicdIcfgr3        = NOVA_GICD_ICFGR3;
inline constexpr std::uint64_t kGicdIgrpmodr1     = NOVA_GICD_IGRPMODR1;
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
inline constexpr std::uint64_t kGicrIsactiver0   = NOVA_GICR_ISACTIVER0;
inline constexpr std::uint64_t kGicrIcactiver0   = NOVA_GICR_ICACTIVER0;
inline constexpr std::uint64_t kGicrIpriorityr   = NOVA_GICR_IPRIORITYR; // 32 bytes
inline constexpr std::uint64_t kGicrIpriorityEnd = kGicrIpriorityr + kNumPrivate;
inline constexpr std::uint64_t kGicrIcfgr0       = NOVA_GICR_ICFGR0;
inline constexpr std::uint64_t kGicrIcfgr1       = NOVA_GICR_ICFGR1;
inline constexpr std::uint64_t kGicrIgrpmodr0    = NOVA_GICR_IGRPMODR0;

// Read-only identification values (emulation policy, not architecture).
inline constexpr std::uint32_t kGicdCtlrDs = NOVA_GICD_CTLR_DS;
// ITLinesNumber=1 (INTIDs 0..63 implemented) with IDbits advertising
// the architectural 10-bit INTID space so drivers computing the ID
// range (e.g. Linux gic-v3) see the 1020..1023 specials as encodable.
inline constexpr std::uint32_t kGicdTyperValue = (9U << 19U) | 1U;
inline constexpr std::uint32_t kGicrTyperLast  = 1U << 4U;   // highest frame of the VM
inline constexpr std::uint32_t kGicIidrValue   = 0x43B;      // implementer: Arm
inline constexpr std::uint32_t kPidr2GicV3     = 0x3U << 4U; // ArchRev = GICv3

// What a guest GIC driver learns by probing, decoded the way it reads
// the fields. Drivers size their INTID range and pick their register
// view from these two words, and some refuse to bind on a mismatch.
static_assert((kGicdTyperValue & 0x1FU) == 1U &&              // ITLinesNumber = 1: INTIDs 0..63
                  ((kGicdTyperValue >> 19U) & 0x1FU) == 9U && // IDbits: 10-bit space, so 1020..1023 encode
                  (kGicdTyperValue & (1U << 17U)) == 0U &&    // LPIS clear: nothing probes for an ITS
                  kPidr2GicV3 == 0x30U,
              "the distributor advertises a 64-INTID GICv3 with no LPI support");

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

// The frame identity a guest walks the redistributor space by. The two
// copies of the vCPU index are not redundant: the driver matches the
// affinity word against its own MPIDR to find its frame, and stops at
// the one flagged Last — a walk that never sees Last runs off the end.
static_assert(
    [] {
      const std::uint64_t first = redist_typer({.number = 0, .last = false});
      const std::uint64_t last  = redist_typer({.number = 1, .last = true});
      return first == 0U && (first & kGicrTyperLast) == 0U && ((last >> 8U) & 0xFFFFU) == 1U && // Processor_Number
             (last >> 32U) == 1U &&                                                             // Aff0
             (last & kGicrTyperLast) != 0U &&
             redist_typer({}) == kGicrTyperLast; // a single-frame VM's only frame is its last
    }(),
    "GICR_TYPER names the vCPU owning the frame and marks the VM's highest one");

struct RedistFrameRef {
  bool          valid  = false;
  std::size_t   vcpu   = 0; // frame index within the VM
  std::uint64_t offset = 0; // offset within the frame
  RedistId      id{};
};

// A GICR access selects a frame by stride; frames at or past the VM's
// vcpu count are unmapped (RAZ/WI).
[[nodiscard]] constexpr auto decode_redist_frame(std::uint64_t off, std::size_t vcpus) noexcept -> RedistFrameRef {
  const auto frame = static_cast<std::size_t>(off / kGicrFrameSize);
  if (frame >= vcpus) {
    return {};
  }
  return {
      .valid  = true,
      .vcpu   = frame,
      .offset = off % kGicrFrameSize,
      .id     = {.number = static_cast<std::uint32_t>(frame), .last = frame == vcpus - 1U},
  };
}

// Frame selection by stride, including the SGI_base half — it sits
// inside the same stride, so an access there must resolve to the same
// vCPU as the RD_base half rather than to the next frame's registers.
static_assert(
    [] {
      const RedistFrameRef first = decode_redist_frame(0, 4);
      const RedistFrameRef mid   = decode_redist_frame((2 * kGicrFrameSize) + kGicrIsenabler0, 4);
      const RedistFrameRef last  = decode_redist_frame((3 * kGicrFrameSize) + kGicrTyper, 4);
      const RedistFrameRef only  = decode_redist_frame(kGicrWaker, 1);
      const RedistFrameRef sgi   = decode_redist_frame(kGicrFrameSize + kGicrIspendr0, 2);
      return first.valid && first.vcpu == 0U && first.offset == 0U && first.id.number == 0U && !first.id.last &&
             mid.valid && mid.vcpu == 2U && mid.offset == kGicrIsenabler0 && mid.id.number == 2U && !mid.id.last &&
             last.valid && last.vcpu == 3U && last.offset == kGicrTyper && last.id.last && only.valid &&
             only.vcpu == 0U && only.id.last && // a single-vCPU VM has one frame, and it is the last
             sgi.valid && sgi.vcpu == 1U && sgi.offset == kGicrIspendr0 && sgi.offset >= kGicrSgiFrame &&
             !decode_redist_frame(kGicrFrameSize, 1).valid && !decode_redist_frame(2 * kGicrFrameSize, 2).valid &&
             !decode_redist_frame((8 * kGicrFrameSize) + kGicrCtlr, 2).valid &&
             !decode_redist_frame(0, 0).valid; // no vCPU owns any frame
    }(),
    "a redistributor access resolves to the vCPU owning its stride, or to nothing");

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

// Who a trapped ICC_SGI1R write reaches. Every path is clamped to the
// VM's own width: the topology is flat, so a nonzero upper affinity or
// RangeSelector names a CPU outside this VM and must select nobody
// rather than wrap onto a sibling.
static_assert(
    [] {
      const std::uint64_t to_sibling = (3ULL << 24U) | 0b10U; // INTID 3 → vCPU 1
      return sgi1r_intid(to_sibling) == 3U && sgi1r_targets(to_sibling, 0, 2) == 0b10U &&
             sgi1r_targets(0xFFFF, 0, 2) == 0b11U &&             // slots past the VM's vCPU count are dropped
             sgi1r_targets(0b01U, 0, 2) == 0b01U &&              // self-targeting is architectural
             sgi1r_targets(kSgi1rIrm | 0xFFFF, 0, 2) == 0b10U && // IRM: every sibling but the sender
             sgi1r_targets(kSgi1rIrm, 1, 2) == 0b01U && sgi1r_targets((1ULL << 16U) | 1U, 0, 2) == 0U && // Aff1
             sgi1r_targets((1ULL << 32U) | 1U, 0, 2) == 0U &&                                            // Aff2
             sgi1r_targets((1ULL << 48U) | 1U, 0, 2) == 0U &&                                            // Aff3
             sgi1r_targets((1ULL << 44U) | 1U, 0, 2) == 0U; // RangeSelector
    }(),
    "an SGI reaches exactly the sibling vCPUs its target list names, and none outside the VM");

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

// Reset state as the guest finds it. The SGI half of the enable word is
// the one that must already be set: a doorbell SGI sent before the guest
// has touched its redistributor would otherwise be undeliverable, and
// nothing re-derives it once the guest enables the bit.
static_assert(
    [] {
      const RedistState reset{};
      return reset.isenabler0 == 0xFFFFU && // SGIs 0..15 enabled, PPIs off until the guest asks
             reset.igroupr0 == ~0U &&       // all private interrupts in Group 1
             reset.pending == 0U && reset.asleep;
    }(),
    "a redistributor resets with the SGIs enabled and everything else quiet");

struct MmioRead {
  bool          known = false;
  std::uint64_t value = 0;
};

// Write outcome: `delivery` marks writes that can change what refill
// would deliver or where (enable/pending/route) — the component fans
// reevaluation out to the VM's vCPUs only for those. Priority, group,
// config and active-state writes are stored or ignored without any
// effect on the deliverable set.
struct WriteResult {
  bool known    = false;
  bool delivery = false;

  [[nodiscard]] constexpr explicit operator bool() const noexcept { return known; }
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

// Offsets the guest may touch that this model deliberately does not
// keep: configuration the DS=1 profile fixes, and active state that
// lives in the list registers. A read answers zero and a write lands
// nowhere, so both directions read the one list rather than each
// carrying a copy that the other can drift from.
inline constexpr std::array kDistIgnored{kGicdIcfgr2, kGicdIcfgr3, kGicdIgrpmodr1, kGicdIsactiver1, kGicdIcactiver1};
inline constexpr std::array kRedistIgnored{kGicrIcfgr0, kGicrIcfgr1, kGicrIgrpmodr0, kGicrIsactiver0, kGicrIcactiver0};

[[nodiscard]] constexpr auto is_ignored(std::span<const std::uint64_t> offsets, std::uint64_t off) noexcept -> bool {
  for (const std::uint64_t candidate : offsets) {
    if (candidate == off) {
      return true;
    }
  }
  return false;
}

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
    // DS is RO-set: one security state is all there is, and guest GIC
    // drivers pick their register view (and may refuse to boot) by it.
    return {.known = true, .value = d.ctlr | kGicdCtlrDs};
  case kGicdTyper:
    return {.known = true, .value = kGicdTyperValue};
  case kGicdTyper2:
    return {.known = true, .value = 0}; // no extended features (pre-GICv3.1 shape)
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
  default:
    return is_ignored(kDistIgnored, off) ? MmioRead{.known = true, .value = 0} : MmioRead{};
  }
}

// `keep_pending` marks SPI bits an ICPENDR1 clear must not drop: an
// SPI whose EoI token is still live is by construction not yet in any
// LR (refill consumes the token), so losing its pending bit would lose
// the interrupt and its EOI-driven rearm with it.
[[nodiscard]] inline auto dist_write(DistState& d, std::uint64_t off, std::uint32_t size, std::uint64_t value,
                                     std::uint32_t keep_pending = 0) noexcept -> WriteResult {
  if (off >= kGicdIpriorityrSpi && off + size <= kGicdIpriorityrEnd) {
    detail::prio_write(d.spi_prio, off - kGicdIpriorityrSpi, size, value);
    return {.known = true}; // priority orders delivery, never gates it
  }
  if (off >= kGicdIrouterSpi && off < kGicdIrouterEnd) {
    if ((off % 8U) == 0U) {
      d.spi_route[(off - kGicdIrouterSpi) / 8U] = static_cast<std::uint8_t>(value); // Aff0 only, IRM ignored
      return {.known = true, .delivery = true};                                     // the SPI target moved
    }
    return {.known = true}; // high word is WI in the flat topology
  }
  const auto word = static_cast<std::uint32_t>(value);
  switch (off) {
  case kGicdCtlr:
    d.ctlr = word;
    return {.known = true};
  case kGicdIgroupr1:
    d.spi_group = word;
    return {.known = true}; // stored for read-back, never gates delivery
  case kGicdIsenabler1:
    d.spi_enabled |= word; // write-1-to-set
    return {.known = true, .delivery = true};
  case kGicdIcenabler1:
    d.spi_enabled &= ~word; // write-1-to-clear
    return {.known = true, .delivery = true};
  case kGicdIspendr1:
    d.spi_pending |= word;
    return {.known = true, .delivery = true};
  case kGicdIcpendr1:
    d.spi_pending = (d.spi_pending & ~word) | keep_pending;
    return {.known = true, .delivery = true};
  default:
    // Accepted and dropped: fixed level/group, active state in the LRs.
    return is_ignored(kDistIgnored, off) ? WriteResult{.known = true} : WriteResult{};
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
  default:
    return is_ignored(kRedistIgnored, off) ? MmioRead{.known = true, .value = 0} : MmioRead{};
  }
}

[[nodiscard]] inline auto redist_write(RedistState& r, std::uint64_t off, std::uint32_t size,
                                       std::uint64_t value) noexcept -> WriteResult {
  if (off >= kGicrIpriorityr && off + size <= kGicrIpriorityEnd) {
    detail::prio_write(r.prio, off - kGicrIpriorityr, size, value);
    return {.known = true}; // priority orders delivery, never gates it
  }
  const auto word = static_cast<std::uint32_t>(value);
  switch (off) {
  case kGicrCtlr:
    return {.known = true}; // no LPI support — WI
  case kGicrWaker:
    r.asleep = (word & kWakerProcessorSleep) != 0U;
    return {.known = true};
  case kGicrIgroupr0:
    r.igroupr0 = word;
    return {.known = true}; // stored for read-back, never gates delivery
  case kGicrIsenabler0:
    r.isenabler0 |= word; // write-1-to-set
    return {.known = true, .delivery = true};
  case kGicrIcenabler0:
    r.isenabler0 &= ~word; // write-1-to-clear
    return {.known = true, .delivery = true};
  case kGicrIspendr0:
    r.pending |= word;
    return {.known = true, .delivery = true};
  case kGicrIcpendr0:
    r.pending &= ~word;
    return {.known = true, .delivery = true};
  default:
    // Accepted and dropped: fixed config/group, active state in the LRs.
    return is_ignored(kRedistIgnored, off) ? WriteResult{.known = true} : WriteResult{};
  }
}

} // namespace nova::vgic
