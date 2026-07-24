#include "dma_device/dma_device.hpp"

#include "dma_device/lifecycle_model.hpp"
#include "hal/console.hpp"
#include "hal/dma_device.hpp"
#include "hal/gic.hpp"
#include "hal/timer.hpp"
#include "nova/abi/dma.hpp"
#include "nova/abi/guest.hpp"
#include "nova/sync.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <string_view>

namespace nova::dma_device {
namespace {

inline constexpr std::size_t   kMaxDevices = 8;
inline constexpr std::uint64_t kTimeoutMs  = 2'000;

namespace device = hw::device;

sync::SpinLock        g_lock;
Registry<kMaxDevices> g_registry;
bool                  g_registry_valid = false;

[[nodiscard]] auto deadline_after_ms(std::uint64_t milliseconds) noexcept -> std::uint64_t {
  return hyp_timer::now() + (hyp_timer::freq() / 1000U) * milliseconds;
}

[[nodiscard]] constexpr auto backend_known(dma::DeviceId device_id) noexcept -> bool {
  return device_id == device::kDmaDeviceId;
}

[[nodiscard]] auto backend_present(dma::DeviceId device_id) noexcept -> bool {
  return backend_known(device_id) && device::present();
}

[[nodiscard]] auto backend_configure(dma::DeviceId device_id) noexcept -> bool {
  return backend_known(device_id) && device::configure_bar();
}

[[nodiscard]] auto backend_disable(dma::DeviceId device_id) noexcept -> bool {
  return backend_known(device_id) && device::disable_bus_master();
}

[[nodiscard]] auto backend_enable(dma::DeviceId device_id) noexcept -> bool {
  return backend_known(device_id) && device::enable_bus_master();
}

[[nodiscard]] auto backend_running(dma::DeviceId device_id) noexcept -> bool {
  return backend_known(device_id) && device::dma_running();
}

[[nodiscard]] auto backend_start(dma::DeviceId device_id, std::uint64_t source, std::uint64_t destination,
                                 std::uint64_t count, bool to_ram) noexcept -> bool {
  return backend_known(device_id) && device::start_dma(source, destination, count, to_ram);
}

void backend_clear_interrupts(dma::DeviceId device_id) noexcept {
  if (backend_known(device_id)) {
    device::clear_interrupts();
  }
}

[[nodiscard]] auto interrupt_for_device(dma::DeviceId device_id) noexcept -> const dma::DeviceInterrupt* {
  for (const dma::DeviceInterrupt& interrupt : dma::device_interrupt_table()) {
    if (interrupt.device_id == device_id) {
      return &interrupt;
    }
  }
  return nullptr;
}

[[nodiscard]] auto interrupt_for_physical(std::uint32_t intid) noexcept -> const dma::DeviceInterrupt* {
  for (const dma::DeviceInterrupt& interrupt : dma::device_interrupt_table()) {
    if (interrupt.physical_intid == intid) {
      return &interrupt;
    }
  }
  return nullptr;
}

void mask_interrupt(dma::DeviceId device_id) noexcept {
  if (const dma::DeviceInterrupt* interrupt = interrupt_for_device(device_id); interrupt != nullptr) {
    static_cast<void>(gic::mask_spi(interrupt->physical_intid));
  }
}

void reset_interrupt(dma::DeviceId device_id) noexcept {
  if (const dma::DeviceInterrupt* interrupt = interrupt_for_device(device_id); interrupt != nullptr) {
    static_cast<void>(gic::mask_spi(interrupt->physical_intid));
    backend_clear_interrupts(device_id);
    static_cast<void>(gic::clear_pending_spi(interrupt->physical_intid));
  }
}

[[nodiscard]] auto prepare_interrupt(dma::DeviceId device_id, std::size_t vm) noexcept -> bool {
  const dma::DeviceInterrupt* interrupt = interrupt_for_device(device_id);
  if (interrupt == nullptr) {
    return true;
  }
  reset_interrupt(device_id);
  const auto guests = guest_table();
  const auto trigger =
      interrupt->trigger == dma::InterruptTrigger::kLevel ? gic::SpiTrigger::kLevel : gic::SpiTrigger::kEdge;
  return vm < guests.size() && gic::configure_spi(interrupt->physical_intid, guests[vm].cpu[0], trigger);
}

void log_state(std::size_t vm, std::string_view state, std::uint64_t generation = 0) noexcept {
  console::write("[dma] VM ");
  console::write_dec64(vm);
  console::write(" ");
  console::write(state);
  if (generation != 0U) {
    console::write(" generation ");
    console::write_dec64(generation);
  }
  console::write("\n");
}

void fail_vm(std::size_t vm, const char* reason) noexcept {
  std::array<dma::DeviceId, kMaxDevices> device_ids{};
  std::size_t                            count = 0;
  {
    sync::Guard guard{g_lock};
    for (Entry& entry : g_registry.entries()) {
      if (entry.owner_vm != vm || entry.state == State::kUnavailable) {
        continue;
      }
      entry.state              = State::kFailed;
      entry.generation         = 0;
      entry.bus_master_blocked = true;
      device_ids[count++]      = entry.device_id;
    }
  }
  for (std::size_t i = 0; i < count; ++i) {
    reset_interrupt(device_ids[i]);
    static_cast<void>(backend_disable(device_ids[i]));
  }
  static_cast<void>(smmu::quarantine_vm(vm));
  console::write("[dma] VM ");
  console::write_dec64(vm);
  console::write(" isolated: ");
  console::write(reason);
  console::write("\n");
}

[[nodiscard]] auto complete_quiesce(std::size_t vm) noexcept -> QuiesceResult {
  std::array<dma::DeviceId, kMaxDevices> device_ids{};
  std::size_t                            count = 0;
  {
    sync::Guard guard{g_lock};
    for (const Entry& entry : g_registry.entries()) {
      if (entry.owner_vm != vm || entry.state == State::kUnavailable || entry.state == State::kQuiesced) {
        continue;
      }
      if (entry.state == State::kFailed) {
        return QuiesceResult::kFailed;
      }
      if (entry.state == State::kDetaching || entry.state != State::kQuiescing || !entry.bus_master_blocked) {
        return QuiesceResult::kPending;
      }
      device_ids[count++] = entry.device_id;
    }
  }

  for (std::size_t i = 0; i < count; ++i) {
    device::acquire_memory();
    if (backend_running(device_ids[i])) {
      return QuiesceResult::kPending;
    }
  }

  bool detach_required = false;
  {
    sync::Guard guard{g_lock};
    for (Entry& entry : g_registry.entries()) {
      if (entry.owner_vm != vm || entry.state == State::kUnavailable || entry.state == State::kQuiesced) {
        continue;
      }
      if (entry.state != State::kQuiescing || !entry.bus_master_blocked) {
        return entry.state == State::kFailed ? QuiesceResult::kFailed : QuiesceResult::kPending;
      }
      entry.state     = State::kDetaching;
      detach_required = true;
    }
  }
  if (!detach_required) {
    return QuiesceResult::kComplete;
  }
  if (!smmu::detach_vm(vm)) {
    fail_vm(vm, "stream detach");
    return QuiesceResult::kFailed;
  }
  static_cast<void>(smmu::poll_events());
  {
    sync::Guard guard{g_lock};
    for (Entry& entry : g_registry.entries()) {
      if (entry.owner_vm == vm && entry.state == State::kDetaching) {
        entry.state      = State::kQuiesced;
        entry.generation = 0;
      }
    }
  }
  log_state(vm, "quiesced");
  return QuiesceResult::kComplete;
}

} // namespace

void init() noexcept {
  {
    sync::Guard guard{g_lock};
    g_registry.reset();
    g_registry_valid = true;
    for (const dma::Assignment& assignment : dma::assignment_table()) {
      if (!g_registry.add(assignment.device_id, assignment.vm)) {
        g_registry_valid = false;
        console::write("[dma] device registry configuration failed\n");
        return;
      }
    }
  }

  for (Entry& entry : g_registry.entries()) {
    if (!backend_present(entry.device_id)) {
      entry.state = backend_known(entry.device_id) ? State::kUnavailable : State::kFailed;
      continue;
    }
    if (!backend_configure(entry.device_id)) {
      console::write("[dma] device configuration failed\n");
      entry.state = State::kFailed;
      continue;
    }
    if (!prepare_interrupt(entry.device_id, entry.owner_vm)) {
      console::write("[dma] interrupt configuration failed\n");
      entry.state = State::kFailed;
      continue;
    }
    entry.state              = State::kQuiesced;
    entry.bus_master_blocked = true;
  }
}

auto begin_quiesce(std::size_t vm) noexcept -> QuiesceResult {
  std::array<dma::DeviceId, kMaxDevices> device_ids{};
  std::size_t                            count   = 0;
  bool                                   pending = false;
  for (const dma::Assignment& assignment : dma::assignment_table()) {
    if (assignment.vm == vm) {
      mask_interrupt(assignment.device_id);
    }
  }
  {
    sync::Guard guard{g_lock};
    if (!g_registry_valid) {
      return QuiesceResult::kFailed;
    }
    for (Entry& entry : g_registry.entries()) {
      if (entry.owner_vm != vm || entry.state == State::kUnavailable || entry.state == State::kQuiesced) {
        continue;
      }
      if (entry.state == State::kFailed) {
        return QuiesceResult::kFailed;
      }
      if (entry.state == State::kQuiescing || entry.state == State::kDetaching) {
        pending = true;
        continue;
      }
      if (entry.state != State::kActive) {
        return QuiesceResult::kFailed;
      }
      entry.state              = State::kQuiescing;
      entry.deadline           = deadline_after_ms(kTimeoutMs);
      entry.bus_master_blocked = false;
      device_ids[count++]      = entry.device_id;
      pending                  = true;
    }
  }

  for (std::size_t i = 0; i < count; ++i) {
    reset_interrupt(device_ids[i]);
    if (!backend_disable(device_ids[i])) {
      fail_vm(vm, "bus-master disable");
      return QuiesceResult::kFailed;
    }
    sync::Guard guard{g_lock};
    if (Entry* entry = g_registry.find(device_ids[i]); entry != nullptr && entry->state == State::kQuiescing) {
      entry->bus_master_blocked = true;
    }
  }
  return pending ? complete_quiesce(vm) : QuiesceResult::kComplete;
}

auto poll_quiesce(std::size_t vm) noexcept -> QuiesceResult {
  std::array<dma::DeviceId, kMaxDevices> device_ids{};
  std::size_t                            count    = 0;
  std::uint64_t                          deadline = UINT64_MAX;
  {
    sync::Guard guard{g_lock};
    if (!g_registry_valid) {
      return QuiesceResult::kFailed;
    }
    for (const Entry& entry : g_registry.entries()) {
      if (entry.owner_vm != vm || entry.state == State::kUnavailable || entry.state == State::kQuiesced) {
        continue;
      }
      if (entry.state == State::kFailed) {
        return QuiesceResult::kFailed;
      }
      if (entry.state == State::kDetaching || entry.state != State::kQuiescing || !entry.bus_master_blocked) {
        return QuiesceResult::kPending;
      }
      device_ids[count++] = entry.device_id;
      if (entry.deadline < deadline) {
        deadline = entry.deadline;
      }
    }
  }
  if (count == 0) {
    return QuiesceResult::kComplete;
  }
  for (std::size_t i = 0; i < count; ++i) {
    if (backend_running(device_ids[i])) {
      if (hyp_timer::now() < deadline) {
        return QuiesceResult::kPending;
      }
      fail_vm(vm, "device drain timeout");
      return QuiesceResult::kFailed;
    }
  }
  return complete_quiesce(vm);
}

auto resume_vm(std::size_t vm, std::uint64_t generation) noexcept -> bool {
  std::array<dma::DeviceId, kMaxDevices> device_ids{};
  std::size_t                            count = 0;
  {
    sync::Guard guard{g_lock};
    if (!g_registry_valid || generation == 0U || g_registry.owner_failed(vm)) {
      return false;
    }
    if (g_registry.owner_active(vm, generation)) {
      return true;
    }
    for (Entry& entry : g_registry.entries()) {
      if (entry.owner_vm != vm || entry.state == State::kUnavailable) {
        continue;
      }
      if (entry.state != State::kQuiesced) {
        return false;
      }
      entry.state         = State::kResuming;
      device_ids[count++] = entry.device_id;
    }
  }
  if (count == 0) {
    return true;
  }

  static_cast<void>(smmu::poll_events());
  if (!smmu::attach_vm(vm, generation)) {
    fail_vm(vm, "stream attach");
    return false;
  }
  for (std::size_t i = 0; i < count; ++i) {
    if (!prepare_interrupt(device_ids[i], vm)) {
      fail_vm(vm, "interrupt prepare");
      return false;
    }
    if (!backend_enable(device_ids[i])) {
      fail_vm(vm, "bus-master enable");
      return false;
    }
  }
  {
    sync::Guard guard{g_lock};
    for (Entry& entry : g_registry.entries()) {
      if (entry.owner_vm == vm && entry.state == State::kResuming) {
        entry.generation         = generation;
        entry.state              = State::kActive;
        entry.bus_master_blocked = false;
      }
    }
  }
  for (std::size_t i = 0; i < count; ++i) {
    if (const dma::DeviceInterrupt* interrupt = interrupt_for_device(device_ids[i]);
        interrupt != nullptr && !gic::unmask_spi(interrupt->physical_intid)) {
      fail_vm(vm, "interrupt unmask");
      return false;
    }
  }
  log_state(vm, "resumed", generation);
  return true;
}

auto can_start(std::size_t vm) noexcept -> bool {
  sync::Guard guard{g_lock};
  return g_registry_valid && !g_registry.owner_failed(vm);
}

auto is_active(std::size_t vm, std::uint64_t generation) noexcept -> bool {
  sync::Guard guard{g_lock};
  return g_registry_valid && g_registry.owner_active(vm, generation);
}

auto start_dma(dma::DeviceId device_id, std::size_t vm, std::uint64_t generation, std::uint64_t source,
               std::uint64_t destination, std::uint64_t count, bool to_ram) noexcept -> bool {
  sync::Guard  guard{g_lock};
  const Entry* entry = g_registry.find(device_id);
  return g_registry_valid && entry != nullptr && entry->owner_vm == vm && entry->state == State::kActive &&
         generation != 0U && entry->generation == generation &&
         backend_start(device_id, source, destination, count, to_ram);
}

} // namespace nova::dma_device

namespace nova {

void dma_device_component::handle_irq(IrqCall* call) noexcept {
  const dma::DeviceInterrupt* interrupt = dma_device::interrupt_for_physical(call->intid);
  if (interrupt == nullptr) {
    return;
  }
  call->handled = true;
  static_cast<void>(gic::mask_spi(interrupt->physical_intid));

  std::size_t   vm         = dma::kNoVm;
  std::uint64_t generation = 0;
  {
    sync::Guard              guard{dma_device::g_lock};
    const dma_device::Entry* entry = dma_device::g_registry.find(interrupt->device_id);
    if (dma_device::g_registry_valid && entry != nullptr && entry->state == dma_device::State::kActive) {
      vm         = entry->owner_vm;
      generation = entry->generation;
    }
  }
  if (vm == dma::kNoVm) {
    return;
  }
  const std::size_t target_vcpu = vgic::spi_target_vcpu(vm, interrupt->virtual_intid);
  if (!vgic::post_tracked(slot_of(vm, target_vcpu), interrupt->virtual_intid, interrupt->physical_intid, generation)) {
    dma_device::fail_vm(vm, "virtual interrupt post");
  }
}

void dma_device_component::handle_virtual_eoi(VirtualEoiCall* call) noexcept {
  const std::size_t vm = vm_of(call->slot);
  for (const dma::DeviceInterrupt& interrupt : dma::device_interrupt_table()) {
    if (interrupt.physical_intid != call->token.physical_intid || interrupt.virtual_intid != call->virtual_intid) {
      continue;
    }
    call->handled = true;
    bool current  = false;
    {
      sync::Guard              guard{dma_device::g_lock};
      const dma_device::Entry* entry = dma_device::g_registry.find(interrupt.device_id);
      current                        = dma_device::g_registry_valid && entry != nullptr && entry->owner_vm == vm &&
                entry->state == dma_device::State::kActive && entry->generation == call->token.generation;
    }
    if (current) {
      static_cast<void>(gic::clear_pending_spi(interrupt.physical_intid));
      if (!gic::unmask_spi(interrupt.physical_intid)) {
        dma_device::fail_vm(vm, "interrupt rearm");
      }
    }
    return;
  }
}

} // namespace nova
