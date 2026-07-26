#include "smmu/smmu.hpp"

#include "core_mmu/stage2_builder.hpp"
#include "hal/console.hpp"
#include "hal/gic.hpp"
#include "hal/smmu.hpp"
#include "nova/abi/dma.hpp"
#include "nova/abi/guest.hpp"
#include "nova/abi/guest_layout.h"
#include "nova/arch/smmuv3/regs.hpp"
#include "nova/fmt.hpp"
#include "nova/panic.hpp"
#include "nova/sync.hpp"
#include "smmu/command_model.hpp"
#include "smmu/dma_table_model.hpp"
#include "smmu/domain_model.hpp"
#include "smmu/fault_model.hpp"
#include "smmu/hw_driver.hpp"
#include "smmu/runtime_model.hpp"
#include "smmu/ste_model.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <span>
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
inline constexpr std::size_t   kDmaL3PoolSize    = 2;
inline constexpr std::uint32_t kPollLimit        = 1'000'000;

inline constexpr std::size_t kStreamTableAlign  = kStreamCount * kStreamTableEntryBytes;
inline constexpr std::size_t kCommandQueueAlign = kCommandCount * sizeof(CommandEntry);
inline constexpr std::size_t kEventQueueAlign   = kEventCount * sizeof(EventRecord);

struct alignas(mmu::k4KiB) DmaTableSet {
  mmu::Table                             l1;
  std::array<mmu::Table, 1>              l2_pool;
  std::array<mmu::Table, kDmaL3PoolSize> l3_pool;
};
static_assert(sizeof(DmaTableSet) % mmu::k4KiB == 0);

struct FaultBatch {
  std::array<std::uint32_t, kEventCount> stream_ids{};
  std::size_t                            count     = 0;
  std::size_t                            processed = 0;
};

// Binds the templated register protocol to the board's SMMU frame.
struct HalHw {
  [[nodiscard]] static auto read32(std::uint32_t offset) noexcept -> std::uint32_t { return hw::read32(offset); }
  static void               write32(std::uint32_t offset, std::uint32_t value) noexcept { hw::write32(offset, value); }
  static void               write64(std::uint32_t offset, std::uint64_t value) noexcept { hw::write64(offset, value); }
  static void               publish_memory() noexcept { hw::publish_memory(); }
  static void               acquire_memory() noexcept { hw::acquire_memory(); }
};

alignas(kStreamTableAlign) std::array<StreamTableEntry, kStreamCount> g_stream_table{};
alignas(kCommandQueueAlign) std::array<CommandEntry, kCommandCount> g_command_queue{};
alignas(kEventQueueAlign) std::array<EventRecord, kEventCount> g_event_queue{};
alignas(mmu::k4KiB) std::array<DmaTableSet, kMaxGuests> g_dma_tables{};

std::array<TranslationContext, kMaxGuests> g_contexts{};
std::array<StreamBinding, kStreamCount>    g_bindings{};
// Owner assignment is fixed at init, so the per-VM stream lists are built
// once and every domain operation walks only its own streams.
StreamIndex<kStreamCount> g_vm_streams{};
// Serializes domain state AND the command queue producer: every
// g_commands.submit caller (attach/detach/quarantine) already holds it,
// and init() runs single-core before guests exist. A submit outside
// this lock would corrupt the ring producer silently.
sync::SpinLock g_domain_lock;
sync::SpinLock g_event_lock;
std::size_t    g_context_count = 0;
bool           g_enabled       = false;
std::uint32_t  g_event_cons    = 0;
std::uint32_t  g_audit_events  = 0;

CommandRing<HalHw> g_commands{
    .entries      = g_command_queue,
    .log2_entries = kCommandQueueLog2,
    .poll_limit   = kPollLimit,
};

[[noreturn]] void fail_init(RuntimeError error, std::uint32_t idr0, std::uint32_t idr1, std::uint32_t idr5) noexcept {
  fmt::HexBuf idr0_text{};
  fmt::HexBuf idr1_text{};
  fmt::HexBuf idr5_text{};
  console::write_parts(std::array{"[smmu] initialization failed: "sv, runtime_error_name(error), " idr0=0x"sv,
                                  fmt::to_hex64(idr0, idr0_text), " idr1=0x"sv, fmt::to_hex64(idr1, idr1_text),
                                  " idr5=0x"sv, fmt::to_hex64(idr5, idr5_text), "\n"sv});
  halt();
}

[[noreturn]] void fail_init(std::string_view reason) noexcept {
  console::write_parts(std::array{"[smmu] initialization failed: "sv, reason, "\n"sv});
  halt();
}

[[noreturn]] void fail_runtime(std::string_view reason) noexcept {
  console::write_parts(std::array{"[smmu] isolation failure: "sv, reason, "\n"sv});
  halt();
}

// Init diagnostics for every register step of the bring-up sequence.
[[nodiscard]] constexpr auto bring_up_reason(BringUpStep step) noexcept -> std::string_view {
  switch (step) {
  case BringUpStep::kNone:
    return "none"sv;
  case BringUpStep::kGbpa:
    return "GBPA timeout"sv;
  case BringUpStep::kDisable:
    return "disable timeout"sv;
  case BringUpStep::kCommandQueue:
    return "command queue timeout"sv;
  case BringUpStep::kStreamInvalidation:
    return "stream cache invalidation"sv;
  case BringUpStep::kTlbInvalidation:
    return "translation cache invalidation"sv;
  case BringUpStep::kEventQueue:
    return "event queue timeout"sv;
  case BringUpStep::kIrqEnable:
  case BringUpStep::kEnable:
    return "enable timeout"sv;
  }
  return "unknown"sv;
}

[[nodiscard]] auto build_dma_contexts(const Capabilities& caps) noexcept -> bool {
  const auto guests      = guest_table();
  const auto assignments = dma::assignment_table();
  const auto devices     = dma::device_stream_table();
  if (guests.empty() || guests.size() > kMaxGuests) {
    return false;
  }

  // The project owns which physical ranges are off-limits to DMA; it
  // builds them from the same board reservations and snapshot packing
  // rule core_mmu bounds-checks (RuntimeStart runs core_mmu first).
  const auto protected_pa = dma::protected_pa_table();
  if (!dma::validate_policy(assignments, devices, guests, {.sid_bits = kSidBits, .protected_pa = protected_pa}).ok()) {
    return false;
  }

  g_contexts.fill(TranslationContext{});
  g_bindings.fill(StreamBinding{});
  g_vm_streams = {};
  for (std::size_t vm = 0; vm < guests.size(); ++vm) {
    DmaTableSet& set = g_dma_tables[vm];

    const std::array<std::uint64_t, 1> l2_pas{
        reinterpret_cast<std::uint64_t>(&set.l2_pool[0]),
    };
    std::array<std::uint64_t, kDmaL3PoolSize> l3_pas{};
    for (std::size_t i = 0; i < kDmaL3PoolSize; ++i) {
      l3_pas[i] = reinterpret_cast<std::uint64_t>(&set.l3_pool[i]);
    }
    mmu::Stage2Tables tables{
        .l1          = &set.l1,
        .l2_pool     = set.l2_pool,
        .l2_pool_pas = l2_pas,
        .l3_pool     = set.l3_pool,
        .l3_pool_pas = l3_pas,
    };
    if (!build_dma_table(tables, guests[vm])) {
      return false;
    }
    g_contexts[vm] = {
        .owner_vm = vm,
        .vmid     = guests[vm].vmid,
        .root_pa  = reinterpret_cast<std::uint64_t>(&set.l1),
    };
  }
  g_context_count = guests.size();
  if (validate_contexts(std::span{g_contexts}.first(g_context_count), guests, caps.vmid16) != ContextError::kNone) {
    return false;
  }

  for (const dma::Assignment& assignment : assignments) {
    if (assignment.stream_id >= g_bindings.size() ||
        !configure_binding(g_bindings[assignment.stream_id], assignment.vm, guests.size())) {
      return false;
    }
    g_stream_table[assignment.stream_id] = make_abort_ste();
  }
  g_vm_streams = build_stream_index<kStreamCount>(g_bindings);
  hw::publish_memory();
  return true;
}

[[nodiscard]] auto abort_stream(std::uint32_t stream_id, std::uint16_t vmid) noexcept -> bool {
  g_stream_table[stream_id][0] = make_abort_ste()[0];
  hw::publish_memory();
  const std::array commands{make_cfgi_ste(stream_id), make_tlbi_s12_vmall(vmid)};
  return g_commands.submit(commands);
}

// SMMUv3 §3.21 configuration update: an STE may not be rewritten in
// place while valid. Take it invalid, invalidate the config cache, fill
// the body, then make it valid and invalidate again. The abort STE this
// replaces is itself V=1, so writing words 1..7 under it relied on the
// SMMU not having cached fields it is architecturally allowed to cache.
[[nodiscard]] auto install_stream(std::uint32_t stream_id, const TranslationContext& context) noexcept -> bool {
  const SteEncoding encoding = make_stage2_ste(context.root_pa, context.vmid);
  if (!encoding.ok()) {
    return false;
  }

  g_stream_table[stream_id][0] = 0; // V=0
  hw::publish_memory();
  const std::array<CommandEntry, 1> invalidate{make_cfgi_ste(stream_id)};
  if (!g_commands.submit(invalidate)) {
    return false;
  }

  for (std::size_t i = 1; i < encoding.entry.size(); ++i) {
    g_stream_table[stream_id][i] = encoding.entry[i];
  }
  hw::publish_memory();
  g_stream_table[stream_id][0] = encoding.entry[0];
  hw::publish_memory();

  const std::array commands{make_cfgi_ste(stream_id), make_tlbi_s12_vmall(context.vmid)};
  return g_commands.submit(commands);
}

[[nodiscard]] auto quarantine_vm_locked(std::size_t vm) noexcept -> bool {
  for (const std::uint16_t sid : g_vm_streams.streams_of(vm)) {
    StreamBinding& binding = g_bindings[sid];
    if (binding.state == DomainState::kQuarantined) {
      continue;
    }
    if (binding.state == DomainState::kAttached &&
        !abort_stream(static_cast<std::uint32_t>(sid), g_contexts[vm].vmid)) {
      return false;
    }
    if (!mark_quarantined(binding)) {
      return false;
    }
  }
  return true;
}

[[nodiscard]] auto quarantine_fault_stream(std::uint32_t stream_id) noexcept -> FaultNotice {
  sync::Guard guard{g_domain_lock};
  if (stream_id >= g_bindings.size() || g_bindings[stream_id].state != DomainState::kAttached) {
    return {};
  }
  const FaultNotice notice = snapshot_fault(g_bindings[stream_id], stream_id);
  if (!notice.valid() || !quarantine_vm_locked(notice.owner_vm)) {
    fail_runtime("fault quarantine");
  }
  return notice;
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

[[nodiscard]] auto drain_event_queue() noexcept -> FaultBatch {
  FaultBatch       batch{};
  const DrainStats stats =
      drain_events<HalHw>(g_event_queue, kEventQueueLog2, g_event_cons, [&batch](const EventRecord& record) noexcept {
        const DecodedEvent event = decode_event(record);
        log_fault(event);
        if (requires_quarantine(event) && batch.count < batch.stream_ids.size()) {
          batch.stream_ids[batch.count++] = event.stream_id;
        }
      });
  if (stats.corrupt) {
    console::write("[smmu] corrupt event queue pointers\n");
    return batch;
  }
  if (stats.overflow) {
    console::write("[smmu] event queue overflow\n");
  }
  batch.processed = stats.processed;
  return batch;
}

void dispatch_faults(const FaultBatch& batch) noexcept {
  // Snapshot and quarantine every affected domain before recovery can
  // reattach a stream at a newer generation.
  const auto notices = collect_fault_notices<kEventCount>(
      std::span<const std::uint32_t>{batch.stream_ids.data(), batch.count}, quarantine_fault_stream);
  for (std::size_t i = 0; i < notices.count; ++i) {
    DmaFaultCall call{.notice = notices.notices[i]};
    cib::service<DmaFaultService>(&call);
    if (!call.handled) {
      console::write("[smmu] DMA fault recovery unavailable\n");
    }
  }
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

  if (const BringUpStep step = shut_down<HalHw>(kPollLimit); step != BringUpStep::kNone) {
    fail_init(bring_up_reason(step));
  }

  const RuntimeLayout layout{
      .stream_table_pa  = reinterpret_cast<std::uint64_t>(g_stream_table.data()),
      .command_queue_pa = reinterpret_cast<std::uint64_t>(g_command_queue.data()),
      .event_queue_pa   = reinterpret_cast<std::uint64_t>(g_event_queue.data()),
      .sid_bits         = kSidBits,
      .command_log2     = kCommandQueueLog2,
      .event_log2       = kEventQueueLog2,
  };
  const Capabilities caps  = decode_capabilities(idr0, idr1, idr5);
  const RuntimeError error = validate_capabilities(caps, layout);
  if (error != RuntimeError::kNone) {
    fail_init(error, idr0, idr1, idr5);
  }

  g_stream_table.fill(StreamTableEntry{});
  g_command_queue.fill(CommandEntry{});
  g_event_queue.fill(EventRecord{});
  g_commands.ready    = false;
  g_commands.producer = 0;
  g_enabled           = false;
  g_event_cons        = 0;
  g_audit_events      = 0;
  if (!build_dma_contexts(caps)) {
    fail_init("DMA contexts");
  }
  hw::publish_memory();

  if (const BringUpStep step = bring_up_translation(g_commands, layout, static_cast<std::uint32_t>(kStreamCount));
      step != BringUpStep::kNone) {
    fail_init(bring_up_reason(step));
  }

  if (!gic::enable_spi(hw::kEventIntid, 0, gic::SpiTrigger::kEdge) ||
      !gic::enable_spi(hw::kCommandIntid, 0, gic::SpiTrigger::kEdge) ||
      !gic::enable_spi(hw::kErrorIntid, 0, gic::SpiTrigger::kEdge)) {
    fail_init("interrupt routing");
  }

  if (const BringUpStep step = enable_faults<HalHw>(kPollLimit); step != BringUpStep::kNone) {
    fail_init(bring_up_reason(step));
  }

  g_enabled = true;
  console::write("[smmu] stage-2 isolation active\n");
}

auto attach_vm(std::size_t vm, std::uint64_t generation) noexcept -> bool {
  sync::Guard guard{g_domain_lock};
  if (!g_commands.ready || vm >= g_context_count || generation == 0U) {
    return false;
  }

  if (!can_attach_vm(g_bindings, g_vm_streams.streams_of(vm), generation)) {
    return false;
  }
  for (const std::uint16_t sid : g_vm_streams.streams_of(vm)) {
    StreamBinding& binding = g_bindings[sid];
    if (binding.state == DomainState::kAttached) {
      continue;
    }
    if (!install_stream(sid, g_contexts[vm]) || !mark_attached(binding, generation)) {
      fail_runtime("attach");
    }
  }
  return true;
}

auto detach_vm(std::size_t vm) noexcept -> bool {
  sync::Guard guard{g_domain_lock};
  if (!g_commands.ready || vm >= g_context_count) {
    return false;
  }

  for (const std::uint16_t sid : g_vm_streams.streams_of(vm)) {
    StreamBinding& binding = g_bindings[sid];
    if (binding.state != DomainState::kAttached) {
      continue;
    }
    if (!abort_stream(sid, g_contexts[vm].vmid) || !mark_detached(binding)) {
      fail_runtime("detach");
    }
  }
  return true;
}

auto quarantine_vm(std::size_t vm) noexcept -> bool {
  sync::Guard guard{g_domain_lock};
  if (!g_commands.ready || vm >= g_context_count) {
    return false;
  }
  if (!quarantine_vm_locked(vm)) {
    fail_runtime("quarantine");
  }
  return true;
}

auto poll_events() noexcept -> std::size_t {
  if (!g_enabled) {
    return 0;
  }
  FaultBatch batch{};
  {
    sync::Guard guard{g_event_lock};
    batch = drain_event_queue();
  }
  dispatch_faults(batch);
  return batch.processed;
}

void handle_irq(IrqCall* call) noexcept {
  if (call->handled || !g_enabled) {
    return;
  }
  if (call->intid == hw::kEventIntid) {
    call->handled = true;
    FaultBatch batch{};
    {
      sync::Guard guard{g_event_lock};
      batch = drain_event_queue();
    }
    dispatch_faults(batch);
  } else if (call->intid == hw::kCommandIntid) {
    call->handled = true;
  } else if (call->intid == hw::kErrorIntid) {
    call->handled = true;
    acknowledge_global_error();
  }
}

} // namespace nova::smmu
