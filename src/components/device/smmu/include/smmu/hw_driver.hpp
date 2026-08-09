#pragma once

// Register-level SMMUv3 protocol: control handshakes, command-queue
// submission, the bring-up sequence and event-queue draining. Every access
// goes through the `Hw` seam (read32/write32/write64/publish_memory/
// acquire_memory), so the whole sequence runs against a fake device on the
// host. No hal/* dependency here — the runtime glue binds the real frame.

#include "nova/arch/smmuv3/regs.hpp"
#include "smmu/command_model.hpp"
#include "smmu/fault_model.hpp"
#include "smmu/queue_model.hpp"
#include "smmu/runtime_model.hpp"

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <span>

namespace nova::smmu {

// Upper bound on the stack chunk used for the bring-up stream invalidation
// batch; the effective batch is min(this, ring capacity - 1), so a queue
// deeper than this trades a few extra syncs for a bounded init frame.
inline constexpr std::size_t kMaxCommandBatch = 32;

// Spin until `offset` reads back `expected`; the SMMU acks control writes
// asynchronously, so every handshake is bounded by the caller's poll limit.
template <typename Hw>
[[nodiscard]] auto wait_for(std::uint32_t offset, std::uint32_t expected, std::uint32_t poll_limit) noexcept -> bool {
  for (std::uint32_t poll = 0; poll < poll_limit; ++poll) {
    if (Hw::read32(offset) == expected) {
      return true;
    }
  }
  return false;
}

template <typename Hw>
[[nodiscard]] auto write_synced(std::uint32_t offset, std::uint32_t ack_offset, std::uint32_t value,
                                std::uint32_t poll_limit) noexcept -> bool {
  Hw::write32(offset, value);
  return wait_for<Hw>(ack_offset, value, poll_limit);
}

// Force unmatched streams to abort instead of bypassing translation. GBPA
// takes an update only while its UPDATE bit is clear, and the write is
// applied asynchronously, so both edges are polled.
template <typename Hw>
[[nodiscard]] auto block_bypass(std::uint32_t poll_limit) noexcept -> bool {
  for (std::uint32_t poll = 0; poll < poll_limit; ++poll) {
    const std::uint32_t current = Hw::read32(regs::kGbpa);
    if ((current & regs::kGbpaUpdate) == 0U) {
      Hw::write32(regs::kGbpa, current | regs::kGbpaUpdate | regs::kGbpaAbort);
      for (std::uint32_t update_poll = 0; update_poll < poll_limit; ++update_poll) {
        if ((Hw::read32(regs::kGbpa) & regs::kGbpaUpdate) == 0U) {
          return true;
        }
      }
      return false;
    }
  }
  return false;
}

// Producer side of the device-shared command queue. `entries` spans the ring
// itself (size == 1 << queue.log2_entries); `queue` carries the ring geometry
// and mirrors CMDQ_PROD, and is the state the caller's lock protects — a
// submit outside that lock would corrupt it silently. Its consumer field is
// a per-submit snapshot of CMDQ_CONS, not state this side owns.
template <typename Hw>
struct CommandRing {
  std::span<CommandEntry> entries;
  QueueState              queue{};
  std::uint32_t           poll_limit = 0;
  bool                    ready      = false;

  // Wait for the device to consume everything published so far. A consumer
  // error latches the queue, so it is a failure rather than a retry.
  [[nodiscard]] auto wait_idle() noexcept -> bool {
    const std::uint32_t mask = queue.pointer_mask();
    for (std::uint32_t poll = 0; poll < poll_limit; ++poll) {
      const std::uint32_t consumer = Hw::read32(regs::kCmdqCons);
      if ((consumer & regs::kCmdqConsErrorMask) != 0U) {
        return false;
      }
      if ((consumer & mask) == queue.producer) {
        Hw::acquire_memory();
        return true;
      }
    }
    return false;
  }

  // Append `commands` plus a trailing CMD_SYNC and wait for completion, so
  // the caller may assume the effects are visible on return. The whole batch
  // is staged first, so a refused one leaves the mirror where the device
  // still sees it.
  [[nodiscard]] auto submit(std::span<const CommandEntry> commands) noexcept -> bool {
    if (!ready || commands.size() + 1U > entries.size() || !wait_idle()) {
      return false;
    }

    QueueState staged = queue;
    staged.consumer   = Hw::read32(regs::kCmdqCons) & queue.pointer_mask();
    for (const CommandEntry& command : commands) {
      entries[staged.producer_index()] = command;
      if (!staged.try_produce()) {
        return false;
      }
    }
    entries[staged.producer_index()] = make_command_sync();
    if (!staged.try_produce()) {
      return false;
    }

    Hw::publish_memory();
    queue = staged;
    Hw::write32(regs::kCmdqProd, queue.producer);
    return wait_idle();
  }
};

// Which bring-up step failed; the glue maps this onto its own diagnostics.
enum class BringUpStep : std::uint8_t {
  kNone,
  kGbpa,
  kDisable,
  kCommandQueue,
  kStreamInvalidation,
  kTlbInvalidation,
  kEventQueue,
  kIrqEnable,
  kEnable,
};

// Park the device before its structures are (re)built: no bypass, no
// interrupts, translation off.
template <typename Hw>
[[nodiscard]] auto shut_down(std::uint32_t poll_limit) noexcept -> BringUpStep {
  if (!block_bypass<Hw>(poll_limit)) {
    return BringUpStep::kGbpa;
  }
  if (!write_synced<Hw>(regs::kIrqCtrl, regs::kIrqAck, 0, poll_limit) ||
      !write_synced<Hw>(regs::kCr0, regs::kCr0Ack, 0, poll_limit)) {
    return BringUpStep::kDisable;
  }
  return BringUpStep::kNone;
}

// Publish the stream table and both queues, then invalidate every stale
// configuration and translation the device may still hold. Stops short of
// enabling faults so the caller can route the GIC SPIs first.
template <typename Hw>
[[nodiscard]] auto bring_up_translation(CommandRing<Hw>& ring, const RuntimeLayout& layout,
                                        std::uint32_t stream_count) noexcept -> BringUpStep {
  Hw::write32(regs::kCr1, kCr1Cacheable);
  Hw::write32(regs::kCr2, kCr2Protected);
  Hw::write64(regs::kStrtabBase, stream_table_base(layout.stream_table_pa));
  Hw::write32(regs::kStrtabBaseCfg, stream_table_config(layout.sid_bits));
  Hw::write64(regs::kCmdqBase, queue_base(layout.command_queue_pa, layout.command_log2));
  Hw::write32(regs::kCmdqProd, 0);
  Hw::write32(regs::kCmdqCons, 0);

  std::uint32_t enables = regs::kCr0CmdqEnable;
  if (!write_synced<Hw>(regs::kCr0, regs::kCr0Ack, enables, ring.poll_limit)) {
    return BringUpStep::kCommandQueue;
  }
  ring.ready = true;
  // Batched invalidation: the queue holds capacity-1 commands plus the
  // trailing CMD_SYNC, so all streams take a few sync round-trips instead of
  // one per SID.
  const std::size_t                          batch_limit = std::min(kMaxCommandBatch, ring.entries.size() - 1U);
  std::array<CommandEntry, kMaxCommandBatch> invalidations{};
  for (std::uint32_t sid = 0; sid < stream_count;) {
    std::size_t batch = 0;
    for (; batch < batch_limit && sid < stream_count; ++batch, ++sid) {
      invalidations[batch] = make_cfgi_ste(sid);
    }
    if (!ring.submit(std::span{invalidations.data(), batch})) {
      return BringUpStep::kStreamInvalidation;
    }
  }
  const std::array<CommandEntry, 1> initial_tlb_invalidation{make_tlbi_nsnh_all()};
  if (!ring.submit(initial_tlb_invalidation)) {
    return BringUpStep::kTlbInvalidation;
  }

  Hw::write64(regs::kEvtqBase, queue_base(layout.event_queue_pa, layout.event_log2));
  Hw::write32(regs::kEvtqProd, 0);
  Hw::write32(regs::kEvtqCons, 0);
  enables |= regs::kCr0EvtqEnable;
  if (!write_synced<Hw>(regs::kCr0, regs::kCr0Ack, enables, ring.poll_limit) ||
      !write_synced<Hw>(regs::kIrqCtrl, regs::kIrqAck, 0, ring.poll_limit)) {
    return BringUpStep::kEventQueue;
  }

  // Errors raised while the device was still unconfigured are stale; ack
  // them so the first real GERROR interrupt is meaningful.
  const std::uint32_t stale_error = Hw::read32(regs::kGerror);
  Hw::write32(regs::kGerrorN, stale_error);
  return BringUpStep::kNone;
}

// Final step: arm the fault interrupts and turn translation on.
template <typename Hw>
[[nodiscard]] auto enable_faults(std::uint32_t poll_limit) noexcept -> BringUpStep {
  if (!write_synced<Hw>(regs::kIrqCtrl, regs::kIrqAck, kFaultIrqs, poll_limit)) {
    return BringUpStep::kIrqEnable;
  }
  if (!write_synced<Hw>(regs::kCr0, regs::kCr0Ack, kEnabledCr0, poll_limit)) {
    return BringUpStep::kEnable;
  }
  return BringUpStep::kNone;
}

struct DrainStats {
  std::size_t processed = 0;
  bool        corrupt   = false;
  bool        overflow  = false;
};

// Walk every event the device published, handing each raw record to
// `on_event`, then release the consumed slots. `consumer` is the caller's
// mirror of EVTQ_CONS and is only advanced once the walk finished.
template <typename Hw, typename OnEvent>
[[nodiscard]] auto drain_events(std::span<const EventRecord> queue_memory, std::uint8_t log2_entries,
                                std::uint32_t& consumer, OnEvent&& on_event) noexcept -> DrainStats {
  DrainStats stats{};
  QueueState queue{
      .log2_entries = log2_entries,
      .producer     = Hw::read32(regs::kEvtqProd),
      .consumer     = consumer,
  };
  if (!queue.consistent()) {
    stats.corrupt = true;
    return stats;
  }

  Hw::acquire_memory();
  while (!queue.empty()) {
    ++stats.processed;
    on_event(queue_memory[queue.consumer_index()]);
    if (!queue.try_consume()) {
      break;
    }
  }

  if (event_overflow_pending(queue)) {
    stats.overflow = true;
    acknowledge_event_overflow(queue);
  }
  consumer = queue.consumer;
  // Advancing EVTQ_CONS frees those slots for the SMMU to overwrite:
  // the reads above must be complete first. The mirror of the barrier
  // submit() uses before publishing CMDQ_PROD.
  Hw::publish_memory();
  Hw::write32(regs::kEvtqCons, consumer);
  return stats;
}

} // namespace nova::smmu
