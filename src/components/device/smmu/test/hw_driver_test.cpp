#include "smmu/hw_driver.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <gtest/gtest.h>
#include <map>
#include <span>
#include <vector>

namespace {

namespace regs = nova::arch::smmuv3;

using nova::smmu::BringUpStep;
using nova::smmu::CommandEntry;
using nova::smmu::CommandRing;
using nova::smmu::DrainStats;
using nova::smmu::EventRecord;
using nova::smmu::RuntimeLayout;

// The device writes records, it does not build them: word 0 carries the
// event type and the stream that raised it.
constexpr auto event_record(nova::smmu::EventType type, std::uint32_t stream_id) noexcept -> EventRecord {
  return {static_cast<std::uint64_t>(type) | (std::uint64_t{stream_id} << nova::smmu::kEventSidShift), 0, 0, 0};
}

// Poll budget that a register never satisfies, for the timeout paths.
inline constexpr std::uint32_t kNever = 0xFFFF'FFFFU;

inline constexpr std::uint32_t kConsumerError = 1U << 24U;

enum class Op : std::uint8_t {
  kRead32,
  kWrite32,
  kWrite64,
  kPublish,
  kAcquire,
};

struct Access {
  Op            op     = Op::kRead32;
  std::uint32_t offset = 0;
  std::uint64_t value  = 0;
};

// A control register whose ack mirror follows the written value only after a
// scripted number of reads. `schedule` gives per-write delays (kNever = the
// device never acks); `delay` applies once the schedule is exhausted.
struct AckRegister {
  std::vector<std::uint32_t> schedule{};
  std::uint32_t              delay     = 0;
  std::uint32_t              value     = 0;
  std::uint32_t              pending   = 0;
  std::uint32_t              remaining = 0;
  std::size_t                writes    = 0;

  void write(std::uint32_t written) {
    pending   = written;
    remaining = writes < schedule.size() ? schedule[writes] : delay;
    ++writes;
  }

  [[nodiscard]] auto read() -> std::uint32_t {
    if (remaining == kNever) {
      return value;
    }
    if (remaining > 0) {
      --remaining;
      return value;
    }
    value = pending;
    return value;
  }
};

// Static register model of an SMMUv3 frame: the Hw seam is a set of static
// functions, so the device state it drives has to be static too.
struct FakeSmmu {
  static inline std::vector<Access>                    log{};
  static inline std::map<std::uint32_t, std::uint64_t> storage{};
  static inline AckRegister                            cr0{};
  static inline AckRegister                            irq{};
  // GBPA reports UPDATE busy for `gbpa_busy` reads before the driver's write
  // and for `gbpa_updating` reads after it.
  static inline std::uint32_t gbpa_value    = 0;
  static inline std::uint32_t gbpa_busy     = 0;
  static inline std::uint32_t gbpa_updating = 0;
  static inline bool          gbpa_written  = false;
  // CMDQ_CONS trails CMDQ_PROD by `cmdq_lag` reads and latches
  // `cmdq_error` once `cmdq_error_after` producer writes happened.
  static inline std::uint32_t cmdq_cons        = 0;
  static inline std::uint32_t cmdq_target      = 0;
  static inline std::uint32_t cmdq_lag         = 0;
  static inline std::uint32_t cmdq_remaining   = 0;
  static inline std::uint32_t cmdq_error       = 0;
  static inline std::uint32_t cmdq_error_after = kNever;
  static inline std::uint32_t prod_writes      = 0;
  // Device side of the command ring: everything the driver published, in
  // submission order, copied out of the shared ring as it is consumed.
  static inline std::span<const CommandEntry> ring_view{};
  static inline std::vector<CommandEntry>     consumed{};
  static inline std::uint32_t                 drained = 0;

  static void reset() {
    log.clear();
    storage.clear();
    cr0              = {};
    irq              = {};
    gbpa_value       = 0;
    gbpa_busy        = 0;
    gbpa_updating    = 0;
    gbpa_written     = false;
    cmdq_cons        = 0;
    cmdq_target      = 0;
    cmdq_lag         = 0;
    cmdq_remaining   = 0;
    cmdq_error       = 0;
    cmdq_error_after = kNever;
    prod_writes      = 0;
    ring_view        = {};
    consumed.clear();
    drained = 0;
  }

  static auto read32(std::uint32_t offset) noexcept -> std::uint32_t {
    const std::uint32_t value = load(offset);
    log.push_back({.op = Op::kRead32, .offset = offset, .value = value});
    return value;
  }

  static void write32(std::uint32_t offset, std::uint32_t value) noexcept {
    log.push_back({.op = Op::kWrite32, .offset = offset, .value = value});
    store(offset, value);
  }

  static void write64(std::uint32_t offset, std::uint64_t value) noexcept {
    log.push_back({.op = Op::kWrite64, .offset = offset, .value = value});
    storage[offset] = value;
  }

  static void publish_memory() noexcept { log.push_back({.op = Op::kPublish}); }

  static void acquire_memory() noexcept { log.push_back({.op = Op::kAcquire}); }

private:
  static auto load(std::uint32_t offset) -> std::uint32_t {
    switch (offset) {
    case regs::kCr0Ack:
      return cr0.read();
    case regs::kIrqAck:
      return irq.read();
    case regs::kGbpa:
      return read_gbpa();
    case regs::kCmdqCons:
      return read_cmdq_cons();
    default:
      return static_cast<std::uint32_t>(storage[offset]);
    }
  }

  static void store(std::uint32_t offset, std::uint32_t value) {
    storage[offset] = value;
    switch (offset) {
    case regs::kCr0:
      cr0.write(value);
      break;
    case regs::kIrqCtrl:
      irq.write(value);
      break;
    case regs::kGbpa:
      gbpa_written = true;
      gbpa_value   = value & ~regs::kGbpaUpdate;
      break;
    case regs::kCmdqProd:
      ++prod_writes;
      consume_to(value);
      cmdq_target    = value;
      cmdq_remaining = cmdq_lag;
      if (prod_writes >= cmdq_error_after) {
        cmdq_error = kConsumerError;
      }
      break;
    case regs::kCmdqCons:
      cmdq_cons      = value;
      cmdq_target    = value;
      cmdq_remaining = 0;
      break;
    default:
      break;
    }
  }

  static auto read_gbpa() -> std::uint32_t {
    if (!gbpa_written) {
      if (gbpa_busy > 0) {
        --gbpa_busy;
        return gbpa_value | regs::kGbpaUpdate;
      }
      return gbpa_value;
    }
    if (gbpa_updating == kNever) {
      return gbpa_value | regs::kGbpaUpdate;
    }
    if (gbpa_updating > 0) {
      --gbpa_updating;
      return gbpa_value | regs::kGbpaUpdate;
    }
    return gbpa_value;
  }

  static auto read_cmdq_cons() -> std::uint32_t {
    if (cmdq_remaining == kNever) {
      return cmdq_cons | cmdq_error;
    }
    if (cmdq_remaining > 0) {
      --cmdq_remaining;
    } else {
      cmdq_cons = cmdq_target;
    }
    return cmdq_cons | cmdq_error;
  }

  // Drain the shared ring up to the newly published producer pointer.
  static void consume_to(std::uint32_t producer) {
    if (ring_view.empty()) {
      return;
    }
    const std::uint32_t index_mask   = static_cast<std::uint32_t>(ring_view.size()) - 1U;
    const std::uint32_t pointer_mask = (static_cast<std::uint32_t>(ring_view.size()) << 1U) - 1U;
    while ((drained & pointer_mask) != (producer & pointer_mask)) {
      consumed.push_back(ring_view[drained & index_mask]);
      drained = (drained + 1U) & pointer_mask;
    }
  }
};

inline constexpr std::size_t kMissing = static_cast<std::size_t>(-1);

[[nodiscard]] auto is_write(const Access& access) -> bool {
  return access.op == Op::kWrite32 || access.op == Op::kWrite64;
}

// Index of the first write to `offset`, kMissing when it never happened.
[[nodiscard]] auto write_index(std::uint32_t offset) -> std::size_t {
  for (std::size_t i = 0; i < FakeSmmu::log.size(); ++i) {
    if (is_write(FakeSmmu::log[i]) && FakeSmmu::log[i].offset == offset) {
      return i;
    }
  }
  return kMissing;
}

// Index of the first write of exactly `value` to `offset`.
[[nodiscard]] auto write_index(std::uint32_t offset, std::uint64_t value) -> std::size_t {
  for (std::size_t i = 0; i < FakeSmmu::log.size(); ++i) {
    const Access& access = FakeSmmu::log[i];
    if (is_write(access) && access.offset == offset && access.value == value) {
      return i;
    }
  }
  return kMissing;
}

// Index of the first barrier of `op`, kMissing when it never happened.
// The ordering assertions below are all of the shape "this barrier came
// before that pointer move", which is the only way the log can show a
// barrier doing its job.
[[nodiscard]] auto barrier_index(Op op) -> std::size_t {
  for (std::size_t i = 0; i < FakeSmmu::log.size(); ++i) {
    if (FakeSmmu::log[i].op == op) {
      return i;
    }
  }
  return kMissing;
}

[[nodiscard]] auto read_count(std::uint32_t offset) -> std::size_t {
  std::size_t count = 0;
  for (const Access& access : FakeSmmu::log) {
    if (access.op == Op::kRead32 && access.offset == offset) {
      ++count;
    }
  }
  return count;
}

[[nodiscard]] auto write_count(std::uint32_t offset) -> std::size_t {
  std::size_t count = 0;
  for (const Access& access : FakeSmmu::log) {
    if (is_write(access) && access.offset == offset) {
      ++count;
    }
  }
  return count;
}

class SmmuHwDriver : public ::testing::Test {
protected:
  static constexpr std::uint8_t  kLog2      = 3;
  static constexpr std::size_t   kEntries   = std::size_t{1} << kLog2;
  static constexpr std::uint64_t kStrtabPa  = 0x8000'0000ULL;
  static constexpr std::uint64_t kCmdqPa    = 0x8010'0000ULL;
  static constexpr std::uint64_t kEvtqPa    = 0x8020'0000ULL;
  static constexpr std::uint32_t kPollLimit = 16;

  void SetUp() override { FakeSmmu::reset(); }

  // A ready ring over the test-owned storage, published to the fake device.
  [[nodiscard]] auto make_ring() -> CommandRing<FakeSmmu> {
    FakeSmmu::ring_view = ring_memory;
    return CommandRing<FakeSmmu>{
        .entries    = ring_memory,
        .queue      = {.log2_entries = kLog2, .producer = 0},
        .poll_limit = kPollLimit,
        .ready      = true,
    };
  }

  [[nodiscard]] static auto layout() -> RuntimeLayout {
    return {
        .stream_table_pa  = kStrtabPa,
        .command_queue_pa = kCmdqPa,
        .event_queue_pa   = kEvtqPa,
        .sid_bits         = kLog2,
        .command_log2     = kLog2,
        .event_log2       = kLog2,
    };
  }

  std::array<CommandEntry, kEntries> ring_memory{};
  std::array<EventRecord, kEntries>  event_memory{};
};

// --- write_synced ---------------------------------------------------------

TEST_F(SmmuHwDriver, WriteSyncedReturnsOnImmediateAck) {
  EXPECT_TRUE(nova::smmu::write_synced<FakeSmmu>(regs::kCr0, regs::kCr0Ack, 0x5, kPollLimit));
  EXPECT_EQ(write_index(regs::kCr0, 0x5), 0U);
  EXPECT_EQ(read_count(regs::kCr0Ack), 1U);
}

TEST_F(SmmuHwDriver, WriteSyncedPollsUntilDelayedAck) {
  FakeSmmu::cr0.delay = 3;
  EXPECT_TRUE(nova::smmu::write_synced<FakeSmmu>(regs::kCr0, regs::kCr0Ack, 0x5, kPollLimit));
  EXPECT_EQ(read_count(regs::kCr0Ack), 4U);
}

TEST_F(SmmuHwDriver, WriteSyncedGivesUpAfterPollLimit) {
  FakeSmmu::cr0.delay = kNever;
  EXPECT_FALSE(nova::smmu::write_synced<FakeSmmu>(regs::kCr0, regs::kCr0Ack, 0x5, 5));
  EXPECT_EQ(read_count(regs::kCr0Ack), 5U);
}

// --- block_bypass --------------------------------------------------------

TEST_F(SmmuHwDriver, BlockBypassRequestsAbortWhileIdle) {
  FakeSmmu::gbpa_value = 0x2;
  EXPECT_TRUE(nova::smmu::block_bypass<FakeSmmu>(kPollLimit));
  EXPECT_NE(write_index(regs::kGbpa, 0x2 | regs::kGbpaUpdate | regs::kGbpaAbort), kMissing);
  EXPECT_EQ(write_count(regs::kGbpa), 1U);
}

TEST_F(SmmuHwDriver, BlockBypassWaitsForAnInFlightUpdate) {
  FakeSmmu::gbpa_busy = 3;
  EXPECT_TRUE(nova::smmu::block_bypass<FakeSmmu>(kPollLimit));
  // Three busy polls, one idle poll, then the abort write.
  EXPECT_EQ(write_index(regs::kGbpa), 4U);
}

TEST_F(SmmuHwDriver, BlockBypassFailsWhenUpdateNeverCompletes) {
  FakeSmmu::gbpa_updating = kNever;
  EXPECT_FALSE(nova::smmu::block_bypass<FakeSmmu>(4));
  EXPECT_EQ(write_count(regs::kGbpa), 1U);
}

// --- CommandRing ---------------------------------------------------------

TEST_F(SmmuHwDriver, SubmitRejectedBeforeTheQueueIsEnabled) {
  CommandRing<FakeSmmu> ring = make_ring();
  ring.ready                 = false;

  const std::array<CommandEntry, 1> commands{nova::smmu::make_cfgi_ste(1)};
  EXPECT_FALSE(ring.submit(commands));
  EXPECT_TRUE(FakeSmmu::log.empty());
}

TEST_F(SmmuHwDriver, SubmitAppendsCommandsThenSync) {
  CommandRing<FakeSmmu> ring = make_ring();

  const std::array commands{nova::smmu::make_cfgi_ste(1), nova::smmu::make_tlbi_s12_vmall(7)};
  ASSERT_TRUE(ring.submit(commands));

  EXPECT_EQ(ring_memory[0], commands[0]);
  EXPECT_EQ(ring_memory[1], commands[1]);
  EXPECT_EQ(ring_memory[2], nova::smmu::make_command_sync());
  EXPECT_EQ(ring.queue.producer, 3U);
  EXPECT_NE(write_index(regs::kCmdqProd, 3U), kMissing);
  // The ring contents must be visible before the producer pointer moves.
  EXPECT_LT(barrier_index(Op::kPublish), write_index(regs::kCmdqProd, 3U));
}

TEST_F(SmmuHwDriver, SubmitWrapsAroundTheRingBoundary) {
  CommandRing<FakeSmmu> ring = make_ring();

  std::vector<CommandEntry> expected{};
  for (std::uint32_t round = 0; round < 3; ++round) {
    const std::array commands{nova::smmu::make_cfgi_ste(round * 3U), nova::smmu::make_cfgi_ste((round * 3U) + 1U),
                              nova::smmu::make_cfgi_ste((round * 3U) + 2U)};
    ASSERT_TRUE(ring.submit(commands));
    expected.insert(expected.end(), commands.begin(), commands.end());
    expected.push_back(nova::smmu::make_command_sync());
  }

  // 12 slots through an 8-entry ring: the third round starts back at slot 0.
  EXPECT_EQ(ring.queue.producer, 12U);
  EXPECT_EQ(FakeSmmu::consumed, expected);
  EXPECT_EQ(ring_memory[0], nova::smmu::make_cfgi_ste(6));
  EXPECT_EQ(ring_memory[3], nova::smmu::make_command_sync());
}

TEST_F(SmmuHwDriver, SubmitRejectsABatchThatCannotHoldItsSync) {
  CommandRing<FakeSmmu> ring = make_ring();

  const std::array<CommandEntry, kEntries> commands{};
  EXPECT_FALSE(ring.submit(commands));
  EXPECT_TRUE(FakeSmmu::log.empty());
}

TEST_F(SmmuHwDriver, SubmitFailsOnConsumerError) {
  FakeSmmu::cmdq_error       = kConsumerError;
  CommandRing<FakeSmmu> ring = make_ring();

  const std::array<CommandEntry, 1> commands{nova::smmu::make_tlbi_nsnh_all()};
  EXPECT_FALSE(ring.submit(commands));
  EXPECT_EQ(write_count(regs::kCmdqProd), 0U);
}

TEST_F(SmmuHwDriver, SubmitWaitsForALaggingConsumer) {
  FakeSmmu::cmdq_lag         = 3;
  CommandRing<FakeSmmu> ring = make_ring();

  const std::array<CommandEntry, 1> commands{nova::smmu::make_tlbi_nsnh_all()};
  ASSERT_TRUE(ring.submit(commands));
  // Idle check, consumer snapshot, then four polls for the published batch.
  EXPECT_EQ(read_count(regs::kCmdqCons), 6U);
}

TEST_F(SmmuHwDriver, SubmitFailsWhenTheConsumerStalls) {
  FakeSmmu::cmdq_lag         = kNever;
  CommandRing<FakeSmmu> ring = make_ring();

  const std::array<CommandEntry, 1> commands{nova::smmu::make_tlbi_nsnh_all()};
  EXPECT_FALSE(ring.submit(commands));
  EXPECT_EQ(write_count(regs::kCmdqProd), 1U);
}

// --- shut_down -----------------------------------------------------------

TEST_F(SmmuHwDriver, ShutDownBlocksBypassThenDisables) {
  // A running device: both acks start non-zero, so the handshakes have to
  // observe a real transition to zero.
  FakeSmmu::irq.value = nova::smmu::kFaultIrqs;
  FakeSmmu::cr0.value = nova::smmu::kEnabledCr0;

  EXPECT_EQ(nova::smmu::shut_down<FakeSmmu>(kPollLimit), BringUpStep::kNone);

  const std::size_t gbpa = write_index(regs::kGbpa);
  const std::size_t irq  = write_index(regs::kIrqCtrl, 0);
  const std::size_t cr0  = write_index(regs::kCr0, 0);
  ASSERT_NE(cr0, kMissing);
  EXPECT_LT(gbpa, irq);
  EXPECT_LT(irq, cr0);
}

TEST_F(SmmuHwDriver, ShutDownReportsGbpaFailure) {
  FakeSmmu::gbpa_updating = kNever;
  EXPECT_EQ(nova::smmu::shut_down<FakeSmmu>(4), BringUpStep::kGbpa);
  EXPECT_EQ(write_count(regs::kIrqCtrl), 0U);
}

TEST_F(SmmuHwDriver, ShutDownReportsIrqDisableFailure) {
  FakeSmmu::irq.value = nova::smmu::kFaultIrqs;
  FakeSmmu::irq.delay = kNever;
  EXPECT_EQ(nova::smmu::shut_down<FakeSmmu>(4), BringUpStep::kDisable);
  EXPECT_EQ(write_count(regs::kCr0), 0U);
}

TEST_F(SmmuHwDriver, ShutDownReportsTranslationDisableFailure) {
  FakeSmmu::cr0.value = nova::smmu::kEnabledCr0;
  FakeSmmu::cr0.delay = kNever;
  EXPECT_EQ(nova::smmu::shut_down<FakeSmmu>(4), BringUpStep::kDisable);
  EXPECT_EQ(write_count(regs::kCr0), 1U);
}

// --- bring_up_translation ------------------------------------------------

TEST_F(SmmuHwDriver, BringUpProgramsStructuresBeforeEnablingThem) {
  constexpr std::uint32_t kStreamCount = 8;
  constexpr std::uint32_t kStaleError  = 0x9;
  FakeSmmu::storage[regs::kGerror]     = kStaleError;
  CommandRing<FakeSmmu> ring           = make_ring();
  ring.ready                           = false;

  ASSERT_EQ(nova::smmu::bring_up_translation(ring, layout(), kStreamCount), BringUpStep::kNone);
  EXPECT_TRUE(ring.ready);

  EXPECT_NE(write_index(regs::kCr1, nova::smmu::kCr1Cacheable), kMissing);
  EXPECT_NE(write_index(regs::kCr2, nova::smmu::kCr2Protected), kMissing);

  // Structures are published before the engine is allowed to fetch them.
  const std::size_t strtab     = write_index(regs::kStrtabBase, nova::smmu::stream_table_base(kStrtabPa));
  const std::size_t strtab_cfg = write_index(regs::kStrtabBaseCfg, kLog2);
  const std::size_t cmdq_base  = write_index(regs::kCmdqBase, nova::smmu::queue_base(kCmdqPa, kLog2));
  const std::size_t cmdq_on    = write_index(regs::kCr0, regs::kCr0CmdqEnable);
  ASSERT_NE(cmdq_on, kMissing);
  EXPECT_LT(strtab, cmdq_on);
  EXPECT_LT(strtab_cfg, cmdq_on);
  EXPECT_LT(cmdq_base, cmdq_on);
  EXPECT_LT(write_index(regs::kCmdqProd, 0), cmdq_on);
  EXPECT_LT(write_index(regs::kCmdqCons, 0), cmdq_on);

  // Every stream got a configuration invalidation, batched behind syncs,
  // and the whole translation cache is flushed last.
  std::vector<CommandEntry> expected{};
  for (std::uint32_t sid = 0; sid < kStreamCount; ++sid) {
    expected.push_back(nova::smmu::make_cfgi_ste(sid));
    // Batch limit is capacity-1 == 7 commands plus the trailing sync.
    if (sid == 6 || sid == kStreamCount - 1) {
      expected.push_back(nova::smmu::make_command_sync());
    }
  }
  expected.push_back(nova::smmu::make_tlbi_nsnh_all());
  expected.push_back(nova::smmu::make_command_sync());
  EXPECT_EQ(FakeSmmu::consumed, expected);

  // The event queue is programmed before its enable, and interrupts stay
  // masked until the caller routes them.
  const std::size_t evtq_base = write_index(regs::kEvtqBase, nova::smmu::queue_base(kEvtqPa, kLog2));
  const std::size_t evtq_on   = write_index(regs::kCr0, regs::kCr0CmdqEnable | regs::kCr0EvtqEnable);
  ASSERT_NE(evtq_on, kMissing);
  EXPECT_LT(cmdq_on, evtq_base);
  EXPECT_LT(evtq_base, evtq_on);
  EXPECT_LT(write_index(regs::kEvtqProd, 0), evtq_on);
  EXPECT_LT(write_index(regs::kEvtqCons, 0), evtq_on);
  EXPECT_LT(evtq_on, write_index(regs::kIrqCtrl, 0));

  // Errors latched while unconfigured are acked last, before GIC routing.
  const std::size_t gerror_ack = write_index(regs::kGerrorN, kStaleError);
  ASSERT_NE(gerror_ack, kMissing);
  EXPECT_EQ(gerror_ack, FakeSmmu::log.size() - 1);
}

TEST_F(SmmuHwDriver, BringUpReportsCommandQueueTimeout) {
  FakeSmmu::cr0.delay        = kNever;
  CommandRing<FakeSmmu> ring = make_ring();
  ring.ready                 = false;

  EXPECT_EQ(nova::smmu::bring_up_translation(ring, layout(), 8), BringUpStep::kCommandQueue);
  EXPECT_FALSE(ring.ready);
  EXPECT_EQ(write_count(regs::kCmdqProd), 1U); // only the reset to zero
}

TEST_F(SmmuHwDriver, BringUpReportsStreamInvalidationFailure) {
  FakeSmmu::cmdq_error       = kConsumerError;
  CommandRing<FakeSmmu> ring = make_ring();
  ring.ready                 = false;

  EXPECT_EQ(nova::smmu::bring_up_translation(ring, layout(), 8), BringUpStep::kStreamInvalidation);
  EXPECT_TRUE(FakeSmmu::consumed.empty());
}

TEST_F(SmmuHwDriver, BringUpReportsTlbInvalidationFailure) {
  // The producer reset plus two stream batches succeed; the error latches on
  // the fourth producer write, which publishes the translation flush.
  FakeSmmu::cmdq_error_after = 4;
  CommandRing<FakeSmmu> ring = make_ring();
  ring.ready                 = false;

  EXPECT_EQ(nova::smmu::bring_up_translation(ring, layout(), 8), BringUpStep::kTlbInvalidation);
  EXPECT_EQ(write_count(regs::kEvtqBase), 0U);
}

TEST_F(SmmuHwDriver, BringUpReportsEventQueueEnableTimeout) {
  FakeSmmu::cr0.schedule     = {0, kNever};
  CommandRing<FakeSmmu> ring = make_ring();
  ring.ready                 = false;

  EXPECT_EQ(nova::smmu::bring_up_translation(ring, layout(), 8), BringUpStep::kEventQueue);
  EXPECT_EQ(write_count(regs::kGerrorN), 0U);
}

TEST_F(SmmuHwDriver, BringUpReportsEventQueueIrqTimeout) {
  FakeSmmu::irq.value        = nova::smmu::kFaultIrqs;
  FakeSmmu::irq.delay        = kNever;
  CommandRing<FakeSmmu> ring = make_ring();
  ring.ready                 = false;

  EXPECT_EQ(nova::smmu::bring_up_translation(ring, layout(), 8), BringUpStep::kEventQueue);
  EXPECT_EQ(write_count(regs::kGerrorN), 0U);
}

// --- enable_faults -------------------------------------------------------

TEST_F(SmmuHwDriver, EnableFaultsArmsInterruptsBeforeTranslation) {
  EXPECT_EQ(nova::smmu::enable_faults<FakeSmmu>(kPollLimit), BringUpStep::kNone);

  const std::size_t irq = write_index(regs::kIrqCtrl, nova::smmu::kFaultIrqs);
  const std::size_t cr0 = write_index(regs::kCr0, nova::smmu::kEnabledCr0);
  ASSERT_NE(cr0, kMissing);
  EXPECT_LT(irq, cr0);
}

TEST_F(SmmuHwDriver, EnableFaultsReportsIrqFailure) {
  FakeSmmu::irq.delay = kNever;
  EXPECT_EQ(nova::smmu::enable_faults<FakeSmmu>(4), BringUpStep::kIrqEnable);
  EXPECT_EQ(write_count(regs::kCr0), 0U);
}

TEST_F(SmmuHwDriver, EnableFaultsReportsTranslationEnableFailure) {
  FakeSmmu::cr0.delay = kNever;
  EXPECT_EQ(nova::smmu::enable_faults<FakeSmmu>(4), BringUpStep::kEnable);
  EXPECT_NE(write_index(regs::kCr0, nova::smmu::kEnabledCr0), kMissing);
}

// --- drain_events --------------------------------------------------------

TEST_F(SmmuHwDriver, DrainEventsDeliversRecordsInOrderAndReleasesSlots) {
  for (std::size_t i = 0; i < event_memory.size(); ++i) {
    event_memory[i] = event_record(nova::smmu::EventType::kTranslationFault, static_cast<std::uint32_t>(i) + 1U);
  }
  FakeSmmu::storage[regs::kEvtqProd] = 3;

  std::uint32_t            consumer = 0;
  std::vector<EventRecord> seen{};
  std::size_t              first_read_at = kMissing;
  const DrainStats         stats         = nova::smmu::drain_events<FakeSmmu>(event_memory, kLog2, consumer,
                                                                              [&seen, &first_read_at](const EventRecord& record) {
                                                                if (first_read_at == kMissing) {
                                                                  first_read_at = FakeSmmu::log.size();
                                                                }
                                                                seen.push_back(record);
                                                              });

  EXPECT_FALSE(stats.corrupt);
  EXPECT_FALSE(stats.overflow);
  EXPECT_EQ(stats.processed, 3U);
  ASSERT_EQ(seen.size(), 3U);
  for (std::size_t i = 0; i < seen.size(); ++i) {
    EXPECT_EQ(nova::smmu::event_stream_id(seen[i]), i + 1U);
  }
  EXPECT_EQ(consumer, 3U);

  // The whole ordering, because each half fails differently: the
  // acquire must precede the record reads or they may predate what the
  // device wrote, and the release must follow them or the device may
  // overwrite a slot still being read. Neither barrier is visible in
  // the result, so without this both can be deleted outright and every
  // gate stays green.
  const std::size_t acquire     = barrier_index(Op::kAcquire);
  const std::size_t release     = barrier_index(Op::kPublish);
  const std::size_t consumed_at = write_index(regs::kEvtqCons, 3U);
  ASSERT_NE(acquire, kMissing);
  ASSERT_NE(release, kMissing);
  ASSERT_NE(first_read_at, kMissing);
  ASSERT_NE(consumed_at, kMissing);
  EXPECT_LT(acquire, first_read_at);
  // The reads themselves are memory, not register traffic, so they
  // leave no entry: the release is the next thing logged after them.
  EXPECT_LE(first_read_at, release);
  EXPECT_LT(release, consumed_at);
}

TEST_F(SmmuHwDriver, DrainEventsRejectsCorruptPointersWithoutConsuming) {
  // used == 9 exceeds the 8-entry capacity: the pointers cannot be trusted.
  FakeSmmu::storage[regs::kEvtqProd] = 9;

  std::uint32_t    consumer = 0;
  std::size_t      calls    = 0;
  const DrainStats stats =
      nova::smmu::drain_events<FakeSmmu>(event_memory, kLog2, consumer, [&calls](const EventRecord&) { ++calls; });

  EXPECT_TRUE(stats.corrupt);
  EXPECT_EQ(stats.processed, 0U);
  EXPECT_EQ(calls, 0U);
  EXPECT_EQ(consumer, 0U);
  EXPECT_EQ(write_count(regs::kEvtqCons), 0U);
}

TEST_F(SmmuHwDriver, DrainEventsAcknowledgesOverflow) {
  FakeSmmu::storage[regs::kEvtqProd] = nova::smmu::kEventQueueOverflow | kEntries;

  std::uint32_t    consumer = 0;
  const DrainStats stats = nova::smmu::drain_events<FakeSmmu>(event_memory, kLog2, consumer, [](const EventRecord&) {});

  EXPECT_TRUE(stats.overflow);
  EXPECT_FALSE(stats.corrupt);
  EXPECT_EQ(stats.processed, kEntries);
  // The overflow phase bit is mirrored back so the device can raise it again.
  const std::uint32_t expected = nova::smmu::kEventQueueOverflow | kEntries;
  EXPECT_EQ(consumer, expected);
  EXPECT_NE(write_index(regs::kEvtqCons, expected), kMissing);
}

} // namespace
