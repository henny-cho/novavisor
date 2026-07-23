#include "smmu/smmu.hpp"

#include "core_mmu/stage2_builder.hpp"
#include "hal/console.hpp"
#include "hal/gic.hpp"
#include "hal/smmu.hpp"
#include "nova/abi/dma.hpp"
#include "nova/abi/guest.hpp"
#include "nova/abi/guest_layout.h"
#include "nova/arch/smmuv3_regs.hpp"
#include "nova/fmt.hpp"
#include "nova/sync.hpp"
#include "nova_panic/nova_panic.hpp"
#include "smmu/command_model.hpp"
#include "smmu/dma_table_model.hpp"
#include "smmu/domain_model.hpp"
#include "smmu/fault_model.hpp"
#include "smmu/queue_model.hpp"
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
  mmu::Table                             l2;
  std::array<mmu::Table, kDmaL3PoolSize> l3_pool;
};
static_assert(sizeof(DmaTableSet) % mmu::k4KiB == 0);

alignas(kStreamTableAlign) std::array<StreamTableEntry, kStreamCount> g_stream_table{};
alignas(kCommandQueueAlign) std::array<CommandEntry, kCommandCount> g_command_queue{};
alignas(kEventQueueAlign) std::array<EventRecord, kEventCount> g_event_queue{};
alignas(mmu::k4KiB) std::array<DmaTableSet, kMaxGuests> g_dma_tables{};

std::array<TranslationContext, kMaxGuests> g_contexts{};
std::array<StreamBinding, kStreamCount>    g_bindings{};
sync::SpinLock                             g_domain_lock;
sync::SpinLock                             g_event_lock;
std::size_t                                g_context_count = 0;
bool                                       g_command_ready = false;
bool                                       g_enabled       = false;
std::uint32_t                              g_command_prod  = 0;
std::uint32_t                              g_event_cons    = 0;
std::uint32_t                              g_audit_events  = 0;

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

[[noreturn]] void fail_runtime(std::string_view reason) noexcept {
  console::write_parts(std::array{"[smmu] isolation failure: "sv, reason, "\n"sv});
  halt();
}

[[nodiscard]] auto wait_for_commands() noexcept -> bool {
  const std::uint32_t pointer_mask = (std::uint32_t{1} << (kCommandQueueLog2 + 1U)) - 1U;
  for (std::uint32_t poll = 0; poll < kPollLimit; ++poll) {
    const std::uint32_t consumer = hw::read32(regs::kCmdqCons);
    if ((consumer & regs::kCmdqConsErrorMask) != 0U) {
      return false;
    }
    if ((consumer & pointer_mask) == g_command_prod) {
      hw::acquire_memory();
      return true;
    }
  }
  return false;
}

[[nodiscard]] auto submit_commands(std::span<const CommandEntry> commands) noexcept -> bool {
  if (!g_command_ready || commands.size() + 1U > kCommandCount || !wait_for_commands()) {
    return false;
  }

  const std::uint32_t pointer_mask = (std::uint32_t{1} << (kCommandQueueLog2 + 1U)) - 1U;
  QueueState          queue{
               .log2_entries = kCommandQueueLog2,
               .producer     = g_command_prod,
               .consumer     = hw::read32(regs::kCmdqCons) & pointer_mask,
  };
  for (const CommandEntry& command : commands) {
    g_command_queue[queue.producer_index()] = command;
    if (!queue.try_produce()) {
      return false;
    }
  }
  g_command_queue[queue.producer_index()] = make_command_sync();
  if (!queue.try_produce()) {
    return false;
  }

  hw::publish_memory();
  g_command_prod = queue.producer;
  hw::write32(regs::kCmdqProd, g_command_prod);
  return wait_for_commands();
}

[[nodiscard]] auto build_dma_contexts(const Capabilities& caps) noexcept -> bool {
  const auto guests      = guest_table();
  const auto assignments = dma::assignment_table();
  if (guests.empty() || guests.size() > kMaxGuests) {
    return false;
  }

  std::uint64_t pristine_size = 0;
  for (const GuestDescriptor& guest : guests) {
    if (pristine_size > UINT64_MAX - guest.ipa_size) {
      return false;
    }
    pristine_size += guest.ipa_size;
  }
  const std::array protected_pa{
      dma::PhysicalRange{.base = 0, .size = NOVA_GUEST_IPA_BASE},
      dma::PhysicalRange{.base = NOVA_IVC_SHM_PA, .size = NOVA_IVC_SHM_SIZE},
      dma::PhysicalRange{.base = NOVA_GUEST_PRISTINE_PA, .size = pristine_size},
  };
  if (!dma::validate_policy(assignments, guests, {.sid_bits = kSidBits, .protected_pa = protected_pa}).ok()) {
    return false;
  }

  g_contexts.fill(TranslationContext{});
  g_bindings.fill(StreamBinding{});
  for (std::size_t vm = 0; vm < guests.size(); ++vm) {
    DmaTableSet& set = g_dma_tables[vm];

    std::array<std::uint64_t, kDmaL3PoolSize> l3_pas{};
    for (std::size_t i = 0; i < kDmaL3PoolSize; ++i) {
      l3_pas[i] = reinterpret_cast<std::uint64_t>(&set.l3_pool[i]);
    }
    mmu::Stage2Tables tables{
        .l1          = &set.l1,
        .l2          = &set.l2,
        .l2_pa       = reinterpret_cast<std::uint64_t>(&set.l2),
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
  hw::publish_memory();
  return true;
}

[[nodiscard]] auto abort_stream(std::uint32_t stream_id, std::uint16_t vmid) noexcept -> bool {
  g_stream_table[stream_id][0] = make_abort_ste()[0];
  hw::publish_memory();
  const std::array commands{make_cfgi_ste(stream_id), make_tlbi_s12_vmall(vmid)};
  return submit_commands(commands);
}

[[nodiscard]] auto install_stream(std::uint32_t stream_id, const TranslationContext& context) noexcept -> bool {
  const SteEncoding encoding = make_stage2_ste(context.root_pa, context.vmid);
  if (!encoding.ok()) {
    return false;
  }
  for (std::size_t i = 1; i < encoding.entry.size(); ++i) {
    g_stream_table[stream_id][i] = encoding.entry[i];
  }
  hw::publish_memory();
  g_stream_table[stream_id][0] = encoding.entry[0];
  hw::publish_memory();

  const std::array commands{make_cfgi_ste(stream_id), make_tlbi_s12_vmall(context.vmid)};
  return submit_commands(commands);
}

[[nodiscard]] auto quarantine_vm_locked(std::size_t vm) noexcept -> bool {
  for (std::size_t sid = 0; sid < g_bindings.size(); ++sid) {
    StreamBinding& binding = g_bindings[sid];
    if (binding.owner_vm != vm || binding.state == DomainState::kQuarantined) {
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

void quarantine_fault_stream(std::uint32_t stream_id) noexcept {
  sync::Guard guard{g_domain_lock};
  if (stream_id >= g_bindings.size() || g_bindings[stream_id].state != DomainState::kAttached) {
    return;
  }
  if (!quarantine_vm_locked(g_bindings[stream_id].owner_vm)) {
    fail_runtime("fault quarantine");
  }
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

[[nodiscard]] auto drain_event_queue() noexcept -> std::size_t {
  std::array<std::uint32_t, kEventCount> quarantine_sids{};
  std::size_t                            quarantine_count = 0;
  std::size_t                            processed        = 0;
  QueueState                             queue{
                                  .log2_entries = kEventQueueLog2,
                                  .producer     = hw::read32(regs::kEvtqProd),
                                  .consumer     = g_event_cons,
  };
  if (!queue.consistent()) {
    console::write("[smmu] corrupt event queue pointers\n");
    return 0;
  }

  hw::acquire_memory();
  while (!queue.empty()) {
    const DecodedEvent event = decode_event(g_event_queue[queue.consumer_index()]);
    ++processed;
    log_fault(event);
    if (requires_quarantine(event) && quarantine_count < quarantine_sids.size()) {
      quarantine_sids[quarantine_count++] = event.stream_id;
    }
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
  for (std::size_t i = 0; i < quarantine_count; ++i) {
    quarantine_fault_stream(quarantine_sids[i]);
  }
  return processed;
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
  const Capabilities caps  = decode_capabilities(idr0, idr1, idr5);
  const RuntimeError error = validate_capabilities(caps, layout);
  if (error != RuntimeError::kNone) {
    fail_init(error);
  }

  g_stream_table.fill(StreamTableEntry{});
  g_command_queue.fill(CommandEntry{});
  g_event_queue.fill(EventRecord{});
  g_command_ready = false;
  g_enabled       = false;
  g_command_prod  = 0;
  g_event_cons    = 0;
  g_audit_events  = 0;
  if (!build_dma_contexts(caps)) {
    fail_init("DMA contexts");
  }
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
  g_command_ready = true;
  for (std::uint32_t sid = 0; sid < kStreamCount; ++sid) {
    const std::array<CommandEntry, 1> invalidation{make_cfgi_ste(sid)};
    if (!submit_commands(invalidation)) {
      fail_init("stream cache invalidation");
    }
  }
  const std::array<CommandEntry, 1> initial_tlb_invalidation{make_tlbi_nsnh_all()};
  if (!submit_commands(initial_tlb_invalidation)) {
    fail_init("translation cache invalidation");
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

auto attach_vm(std::size_t vm, std::uint64_t generation) noexcept -> bool {
  sync::Guard guard{g_domain_lock};
  if (!g_command_ready || vm >= g_context_count || generation == 0U) {
    return false;
  }

  for (const StreamBinding& binding : g_bindings) {
    if (binding.owner_vm != vm) {
      continue;
    }
    if (binding.state == DomainState::kAttached) {
      if (!attachment_matches(binding, generation)) {
        return false;
      }
    } else if (!can_attach(binding, generation)) {
      return false;
    }
  }
  for (std::uint32_t sid = 0; sid < g_bindings.size(); ++sid) {
    StreamBinding& binding = g_bindings[sid];
    if (binding.owner_vm != vm || binding.state == DomainState::kAttached) {
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
  if (!g_command_ready || vm >= g_context_count) {
    return false;
  }

  for (std::uint32_t sid = 0; sid < g_bindings.size(); ++sid) {
    StreamBinding& binding = g_bindings[sid];
    if (binding.owner_vm != vm || binding.state != DomainState::kAttached) {
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
  if (!g_command_ready || vm >= g_context_count) {
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
  sync::Guard guard{g_event_lock};
  return drain_event_queue();
}

void handle_irq(IrqCall* call) noexcept {
  if (!g_enabled) {
    return;
  }
  if (call->intid == hw::kEventIntid) {
    call->handled = true;
    sync::Guard guard{g_event_lock};
    static_cast<void>(drain_event_queue());
  } else if (call->intid == hw::kCommandIntid) {
    call->handled = true;
  } else if (call->intid == hw::kErrorIntid) {
    call->handled = true;
    acknowledge_global_error();
  }
}

} // namespace nova::smmu
