#include "smmu/smmu.hpp"

#include "hal/console.hpp"
#include "hal/gic.hpp"
#include "hal/smmu.hpp"
#include "nova/abi/dma.hpp"
#include "nova/arch/smmuv3_regs.hpp"
#include "nova/fmt.hpp"
#include "nova_panic/nova_panic.hpp"
#include "smmu/fault_model.hpp"
#include "smmu/queue_model.hpp"
#include "smmu/runtime_model.hpp"
#include "smmu/ste_model.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <string_view>

namespace nova::smmu {
namespace {

using namespace std::literals;

inline constexpr std::uint8_t  kSidBits          = 5;
inline constexpr std::uint8_t  kCommandQueueLog2 = 4;
inline constexpr std::uint8_t  kEventQueueLog2   = 4;
inline constexpr std::size_t   kStreamCount      = std::size_t{1} << kSidBits;
inline constexpr std::size_t   kCommandCount     = std::size_t{1} << kCommandQueueLog2;
inline constexpr std::size_t   kEventCount       = std::size_t{1} << kEventQueueLog2;
inline constexpr std::uint32_t kPollLimit        = 1'000'000;

using CommandEntry = std::array<std::uint64_t, 2>;

inline constexpr std::size_t kStreamTableAlign  = kStreamCount * kStreamTableEntryBytes;
inline constexpr std::size_t kCommandQueueAlign = kCommandCount * sizeof(CommandEntry);
inline constexpr std::size_t kEventQueueAlign   = kEventCount * sizeof(EventRecord);

alignas(kStreamTableAlign) std::array<StreamTableEntry, kStreamCount> g_stream_table{};
alignas(kCommandQueueAlign) std::array<CommandEntry, kCommandCount> g_command_queue{};
alignas(kEventQueueAlign) std::array<EventRecord, kEventCount> g_event_queue{};

bool          g_enabled      = false;
std::uint32_t g_event_cons   = 0;
std::uint32_t g_audit_events = 0;

[[nodiscard]] auto wait_for(std::uint32_t offset, std::uint32_t expected) noexcept -> bool {
  for (std::uint32_t poll = 0; poll < kPollLimit; ++poll) {
    if (hw::read32(offset) == expected) {
      return true;
    }
  }
  return false;
}

[[nodiscard]] auto write_synced(std::uint32_t offset, std::uint32_t ack_offset, std::uint32_t value) noexcept -> bool {
  hw::write32(offset, value);
  return wait_for(ack_offset, value);
}

[[nodiscard]] auto block_bypass() noexcept -> bool {
  for (std::uint32_t poll = 0; poll < kPollLimit; ++poll) {
    const std::uint32_t current = hw::read32(regs::kGbpa);
    if ((current & regs::kGbpaUpdate) == 0U) {
      hw::write32(regs::kGbpa, current | regs::kGbpaUpdate | regs::kGbpaAbort);
      for (std::uint32_t update_poll = 0; update_poll < kPollLimit; ++update_poll) {
        if ((hw::read32(regs::kGbpa) & regs::kGbpaUpdate) == 0U) {
          return true;
        }
      }
      return false;
    }
  }
  return false;
}

[[noreturn]] void fail_init(RuntimeError error) noexcept {
  console::write("[smmu] initialization failed error=");
  console::write_dec64(static_cast<std::uint8_t>(error));
  console::write("\n");
  halt();
}

[[noreturn]] void fail_init(std::string_view reason) noexcept {
  console::write_parts(std::array{"[smmu] initialization failed: "sv, reason, "\n"sv});
  halt();
}

void log_fault(const DecodedEvent& event) noexcept {
  if (g_audit_events >= dma::kFaultAuditBurst) {
    if (g_audit_events == dma::kFaultAuditBurst) {
      console::write("[smmu] further fault audit suppressed\n");
    }
    if (g_audit_events != UINT32_MAX) {
      ++g_audit_events;
    }
    return;
  }
  ++g_audit_events;

  fmt::HexBuf type_text{};
  fmt::DecBuf sid_text{};
  fmt::HexBuf address_text{};
  const auto  type = fmt::to_hex64(static_cast<std::uint8_t>(event.type), type_text);
  const auto  sid  = fmt::to_dec64(event.stream_id, sid_text);

  if (event.has_input_address) {
    const auto address = fmt::to_hex64(event.input_address, address_text);
    console::write_parts(std::array{"[smmu] fault type=0x"sv, type, " sid="sv, sid, " iova=0x"sv, address, "\n"sv});
    return;
  }
  if (!event.known) {
    fmt::HexBuf raw0{};
    fmt::HexBuf raw1{};
    fmt::HexBuf raw2{};
    fmt::HexBuf raw3{};
    console::write_parts(std::array{"[smmu] fault type=0x"sv, type, " sid="sv, sid, " raw="sv,
                                    fmt::to_hex64(event.raw[0], raw0), ":"sv, fmt::to_hex64(event.raw[1], raw1), ":"sv,
                                    fmt::to_hex64(event.raw[2], raw2), ":"sv, fmt::to_hex64(event.raw[3], raw3),
                                    "\n"sv});
    return;
  }
  console::write_parts(std::array{"[smmu] fault type=0x"sv, type, " sid="sv, sid, "\n"sv});
}

void drain_event_queue() noexcept {
  QueueState queue{
      .log2_entries = kEventQueueLog2,
      .producer     = hw::read32(regs::kEvtqProd),
      .consumer     = g_event_cons,
  };
  if (!queue.consistent()) {
    console::write("[smmu] corrupt event queue pointers\n");
    return;
  }

  hw::acquire_memory();
  while (!queue.empty()) {
    log_fault(decode_event(g_event_queue[queue.consumer_index()]));
    if (!queue.try_consume()) {
      break;
    }
  }

  if (event_overflow_pending(queue)) {
    console::write("[smmu] event queue overflow\n");
    acknowledge_event_overflow(queue);
  }
  g_event_cons = queue.consumer;
  hw::write32(regs::kEvtqCons, g_event_cons);
}

void acknowledge_global_error() noexcept {
  const std::uint32_t error  = hw::read32(regs::kGerror);
  const std::uint32_t active = error ^ hw::read32(regs::kGerrorN);
  hw::write32(regs::kGerrorN, error);

  fmt::HexBuf active_text{};
  console::write_parts(std::array{"[smmu] global error=0x"sv, fmt::to_hex64(active, active_text), "\n"sv});
}

} // namespace

void init() noexcept {
  const std::uint32_t idr0 = hw::read32(regs::kIdr0);
  const std::uint32_t idr1 = hw::read32(regs::kIdr1);
  const std::uint32_t idr5 = hw::read32(regs::kIdr5);
  if (idr0 == 0U && idr1 == 0U && idr5 == 0U) {
    fail_init("device unavailable");
  }

  if (!block_bypass()) {
    fail_init("GBPA timeout");
  }
  if (!write_synced(regs::kIrqCtrl, regs::kIrqAck, 0) || !write_synced(regs::kCr0, regs::kCr0Ack, 0)) {
    fail_init("disable timeout");
  }

  const RuntimeLayout layout{
      .stream_table_pa  = reinterpret_cast<std::uint64_t>(g_stream_table.data()),
      .command_queue_pa = reinterpret_cast<std::uint64_t>(g_command_queue.data()),
      .event_queue_pa   = reinterpret_cast<std::uint64_t>(g_event_queue.data()),
      .sid_bits         = kSidBits,
      .command_log2     = kCommandQueueLog2,
      .event_log2       = kEventQueueLog2,
  };
  const RuntimeError error = validate_capabilities(decode_capabilities(idr0, idr1, idr5), layout);
  if (error != RuntimeError::kNone) {
    fail_init(error);
  }

  g_stream_table.fill(StreamTableEntry{});
  g_command_queue.fill(CommandEntry{});
  g_event_queue.fill(EventRecord{});
  g_event_cons   = 0;
  g_audit_events = 0;
  hw::publish_memory();

  hw::write32(regs::kCr1, kCr1Cacheable);
  hw::write32(regs::kCr2, kCr2Protected);
  hw::write64(regs::kStrtabBase, stream_table_base(layout.stream_table_pa));
  hw::write32(regs::kStrtabBaseCfg, stream_table_config(kSidBits));
  hw::write64(regs::kCmdqBase, queue_base(layout.command_queue_pa, kCommandQueueLog2));
  hw::write32(regs::kCmdqProd, 0);
  hw::write32(regs::kCmdqCons, 0);

  std::uint32_t enables = regs::kCr0CmdqEnable;
  if (!write_synced(regs::kCr0, regs::kCr0Ack, enables)) {
    fail_init("command queue timeout");
  }

  hw::write64(regs::kEvtqBase, queue_base(layout.event_queue_pa, kEventQueueLog2));
  hw::write32(regs::kEvtqProd, 0);
  hw::write32(regs::kEvtqCons, 0);
  enables |= regs::kCr0EvtqEnable;
  if (!write_synced(regs::kCr0, regs::kCr0Ack, enables) || !write_synced(regs::kIrqCtrl, regs::kIrqAck, 0)) {
    fail_init("event queue timeout");
  }

  const std::uint32_t stale_error = hw::read32(regs::kGerror);
  hw::write32(regs::kGerrorN, stale_error);
  if (!gic::enable_spi(hw::kEventIntid, 0, gic::SpiTrigger::kEdge) ||
      !gic::enable_spi(hw::kCommandIntid, 0, gic::SpiTrigger::kEdge) ||
      !gic::enable_spi(hw::kErrorIntid, 0, gic::SpiTrigger::kEdge)) {
    fail_init("interrupt routing");
  }

  if (!write_synced(regs::kIrqCtrl, regs::kIrqAck, kFaultIrqs) ||
      !write_synced(regs::kCr0, regs::kCr0Ack, kEnabledCr0)) {
    fail_init("enable timeout");
  }

  g_enabled = true;
  console::write("[smmu] stage-2 isolation active\n");
}

void handle_irq(IrqCall* call) noexcept {
  if (!g_enabled) {
    return;
  }
  if (call->intid == hw::kEventIntid) {
    call->handled = true;
    drain_event_queue();
  } else if (call->intid == hw::kCommandIntid) {
    call->handled = true;
  } else if (call->intid == hw::kErrorIntid) {
    call->handled = true;
    acknowledge_global_error();
  }
}

} // namespace nova::smmu
