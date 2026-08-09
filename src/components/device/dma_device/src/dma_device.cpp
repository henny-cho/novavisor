#include "dma_device/dma_device.hpp"

#include "dma_device/backend_model.hpp"
#include "dma_device/lifecycle_model.hpp"
#include "hal/console.hpp"
#include "hal/dma_device.hpp"
#include "hal/gic.hpp"
#include "hal/timer.hpp"
#include "nova/abi/dma.hpp"
#include "nova/abi/guest.hpp"
#include "nova/sync.hpp"
#include "smmu/smmu.hpp"
#include "trace/trace.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <span>
#include <string_view>

namespace nova::dma_device {
namespace {

// The registry's capacity is the ABI's: the image generator refuses an
// inventory above it, so a board cannot describe more devices than this
// tracks.
inline constexpr std::size_t   kMaxDevices = dma::kMaxDevices;
inline constexpr std::uint64_t kTimeoutMs  = 2'000;

namespace device = hw::device;

sync::SpinLock        g_lock;
Registry<kMaxDevices> g_registry;
bool                  g_registry_valid = false;

// dma::InterruptTrigger is the guest-shareable ABI spelling of the same
// two GIC trigger modes as arch::gicv3::SpiTrigger; nova/abi must not
// pull in an arch header, so bridge the two here.
[[nodiscard]] constexpr auto to_spi_trigger(dma::InterruptTrigger trigger) noexcept -> gic::SpiTrigger {
  return trigger == dma::InterruptTrigger::kLevel ? gic::SpiTrigger::kLevel : gic::SpiTrigger::kEdge;
}

[[nodiscard]] auto edu_drained() noexcept -> bool {
  device::acquire_memory();
  return !device::dma_running();
}

inline constexpr std::array<Backend, 1> kBackends{{
    {
        .device_id        = device::kDmaDeviceId,
        .reset_capability = dma::ResetCapability::kQuiesce,
        .present          = device::present,
        .configure        = device::configure_bar,
        .quiesce          = device::disable_bus_master,
        .drained          = edu_drained,
        .reset            = nullptr,
        .resume           = device::enable_bus_master,
        .start_dma        = device::start_dma,
        .clear_interrupts = device::clear_interrupts,
    },
}};

[[nodiscard]] constexpr auto backend_for(dma::DeviceId device_id) noexcept -> const Backend* {
  return find_backend(kBackends, device_id);
}

[[nodiscard]] constexpr auto backend_known(dma::DeviceId device_id) noexcept -> bool {
  return backend_for(device_id) != nullptr;
}

[[nodiscard]] auto backend_present(dma::DeviceId device_id) noexcept -> bool {
  const Backend* backend = backend_for(device_id);
  return backend != nullptr && backend->present();
}

[[nodiscard]] auto backend_configure(dma::DeviceId device_id) noexcept -> bool {
  const Backend* backend = backend_for(device_id);
  return backend != nullptr && backend->configure();
}

[[nodiscard]] auto backend_quiesce(dma::DeviceId device_id) noexcept -> bool {
  const Backend* backend = backend_for(device_id);
  return backend != nullptr && backend->quiesce();
}

[[nodiscard]] auto backend_drained(dma::DeviceId device_id) noexcept -> bool {
  const Backend* backend = backend_for(device_id);
  return backend != nullptr && backend->drained();
}

[[nodiscard]] auto backend_reset(dma::DeviceId device_id) noexcept -> bool {
  const Backend* backend = backend_for(device_id);
  if (backend == nullptr || backend->reset_capability == dma::ResetCapability::kNone) {
    return false;
  }
  return backend->reset_capability == dma::ResetCapability::kQuiesce || (backend->reset != nullptr && backend->reset());
}

[[nodiscard]] auto backend_resume(dma::DeviceId device_id) noexcept -> bool {
  const Backend* backend = backend_for(device_id);
  return backend != nullptr && backend->resume();
}

[[nodiscard]] auto backend_start(dma::DeviceId device_id, std::uint64_t source, std::uint64_t destination,
                                 std::uint64_t count, bool to_ram) noexcept -> bool {
  const Backend* backend = backend_for(device_id);
  return backend != nullptr && backend->start_dma(source, destination, count, to_ram);
}

void backend_clear_interrupts(dma::DeviceId device_id) noexcept {
  if (const Backend* backend = backend_for(device_id); backend != nullptr) {
    backend->clear_interrupts();
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
  return vm < guests.size() &&
         gic::configure_spi(interrupt->physical_intid, guests[vm].cpu[0], to_spi_trigger(interrupt->trigger));
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

// What one walk over a VM's entries found: the devices it collected, and
// the verdict that stopped it. kSkip means nothing stopped it, so the
// collected devices are the whole set that walk is responsible for.
struct ScanOutcome {
  std::array<dma::DeviceId, kMaxDevices> device_ids{};
  std::size_t                            count   = 0;
  ScanAction                             verdict = ScanAction::kSkip;

  [[nodiscard]] constexpr auto complete() const noexcept -> bool { return verdict == ScanAction::kSkip; }

  [[nodiscard]] constexpr auto devices() const noexcept -> std::span<const dma::DeviceId> {
    return {device_ids.data(), count};
  }
};

// The one walk over the entries a VM owns: classify each, run `act` on
// the collected ones, and stop at the first entry the walk cannot act
// on. Runs under g_lock, so `act` must not touch hardware.
template <typename Classify, typename Act>
[[nodiscard]] auto scan_owned(std::size_t vm, Classify classify, Act act) noexcept -> ScanOutcome {
  ScanOutcome outcome{};
  for (Entry& entry : g_registry.entries()) {
    if (entry.owner_vm != vm) {
      continue;
    }
    const ScanAction action = classify(entry);
    switch (action) {
    case ScanAction::kSkip:
      continue;
    case ScanAction::kFail:
    case ScanAction::kPending:
      outcome.verdict = action;
      return outcome;
    case ScanAction::kCollect:
      act(entry);
      outcome.device_ids[outcome.count++] = entry.device_id;
    }
  }
  return outcome;
}

// Both quiesce walks ask the same question of an entry.
[[nodiscard]] auto quiescing_action(const Entry& entry) noexcept -> ScanAction {
  return classify_quiescing(entry.state, entry.bus_master_blocked);
}

// A quiesce walk that stopped answers the caller with its verdict.
[[nodiscard]] constexpr auto quiesce_result(ScanAction verdict) noexcept -> QuiesceResult {
  return verdict == ScanAction::kFail ? QuiesceResult::kFailed : QuiesceResult::kPending;
}

void fail_vm(std::size_t vm, const char* reason) noexcept {
  ScanOutcome scan{};
  {
    sync::Guard guard{g_lock};
    // Isolation touches every device the VM still holds, whatever
    // lifecycle state it is in.
    scan = scan_owned(
        vm,
        [](const Entry& entry) noexcept {
          return entry.state == State::kUnavailable ? ScanAction::kSkip : ScanAction::kCollect;
        },
        [](const Entry&) noexcept {});
    g_registry.fail_owner(vm);
  }
  for (const dma::DeviceId device_id : scan.devices()) {
    reset_interrupt(device_id);
    static_cast<void>(backend_quiesce(device_id));
  }
  static_cast<void>(smmu::quarantine_vm(vm));
  console::write("[dma] VM ");
  console::write_dec64(vm);
  console::write(" isolated: ");
  console::write(reason);
  console::write("\n");
}

[[nodiscard]] auto complete_quiesce(std::size_t vm) noexcept -> QuiesceResult {
  ScanOutcome scan{};
  {
    sync::Guard guard{g_lock};
    scan = scan_owned(vm, quiescing_action, [](const Entry&) noexcept {});
  }
  if (!scan.complete()) {
    return quiesce_result(scan.verdict);
  }

  for (const dma::DeviceId device_id : scan.devices()) {
    if (!backend_drained(device_id)) {
      return QuiesceResult::kPending;
    }
  }
  for (const dma::DeviceId device_id : scan.devices()) {
    if (!backend_reset(device_id)) {
      fail_vm(vm, "device reset");
      return QuiesceResult::kFailed;
    }
  }

  ScanOutcome detaching{};
  {
    sync::Guard guard{g_lock};
    detaching = scan_owned(vm, quiescing_action, [](Entry& entry) noexcept { entry.state = State::kDetaching; });
  }
  if (!detaching.complete()) {
    return quiesce_result(detaching.verdict);
  }
  if (detaching.count == 0) {
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

// Probe and configure one device. Runs unlocked at boot: init is
// single-core, and prepare_interrupt programs the GIC — hardware MMIO
// must not sit under the registry spinlock.
[[nodiscard]] auto probe_device(dma::DeviceId device_id, std::size_t owner_vm) noexcept -> State {
  if (!backend_present(device_id)) {
    return backend_known(device_id) ? State::kUnavailable : State::kFailed;
  }
  if (backend_for(device_id)->reset_capability == dma::ResetCapability::kNone) {
    console::write("[dma] device has no safe reset capability\n");
    return State::kFailed;
  }
  if (!backend_configure(device_id)) {
    console::write("[dma] device configuration failed\n");
    return State::kFailed;
  }
  if (!prepare_interrupt(device_id, owner_vm)) {
    console::write("[dma] interrupt configuration failed\n");
    return State::kFailed;
  }
  return State::kQuiesced;
}

void init() noexcept {
  const BackendPolicyCheck policy =
      validate_backend_policy(dma::assignment_table(), dma::device_capability_table(), kBackends, kMaxDevices);
  bool valid = policy.ok();
  {
    sync::Guard guard{g_lock}; // every registry mutation takes the lock, init included
    valid            = valid && g_registry.load(dma::assignment_table());
    g_registry_valid = valid;
  }
  if (!valid) {
    console::write(policy.ok() ? "[dma] device registry configuration failed\n"
                               : "[dma] backend policy configuration failed\n");
    return;
  }

  for (Entry& entry : g_registry.entries()) {
    const State probed = probe_device(entry.device_id, entry.owner_vm);
    sync::Guard guard{g_lock};
    entry.state              = probed;
    entry.bus_master_blocked = probed == State::kQuiesced;
  }
}

auto begin_quiesce(std::size_t vm) noexcept -> QuiesceResult {
  for (const dma::Assignment& assignment : dma::assignment_table()) {
    if (assignment.vm == vm) {
      mask_interrupt(assignment.device_id);
    }
  }

  ScanOutcome scan{};
  bool        pending = false;
  {
    sync::Guard guard{g_lock};
    if (!g_registry_valid) {
      return QuiesceResult::kFailed;
    }
    scan = scan_owned(
        vm,
        [&pending](const Entry& entry) noexcept {
          const ScanAction action = classify_begin_quiesce(entry.state);
          if (action != ScanAction::kPending) {
            return action;
          }
          // Already mid-quiesce: not this walk's device, but the VM still
          // has to converge through complete_quiesce.
          pending = true;
          return ScanAction::kSkip;
        },
        [](Entry& entry) noexcept {
          entry.state              = State::kQuiescing;
          entry.deadline           = hyp_timer::deadline_after_ms(kTimeoutMs);
          entry.bus_master_blocked = false;
        });
  }
  if (!scan.complete()) {
    return QuiesceResult::kFailed; // the only verdict left that stops this walk
  }

  for (const dma::DeviceId device_id : scan.devices()) {
    reset_interrupt(device_id);
    if (!backend_quiesce(device_id)) {
      fail_vm(vm, "bus-master disable");
      return QuiesceResult::kFailed;
    }
    sync::Guard guard{g_lock};
    if (Entry* entry = g_registry.find(device_id); entry != nullptr && entry->state == State::kQuiescing) {
      entry->bus_master_blocked = true;
    }
  }
  return pending || scan.count != 0 ? complete_quiesce(vm) : QuiesceResult::kComplete;
}

auto poll_quiesce(std::size_t vm) noexcept -> QuiesceResult {
  ScanOutcome   scan{};
  std::uint64_t deadline = UINT64_MAX;
  {
    sync::Guard guard{g_lock};
    if (!g_registry_valid) {
      return QuiesceResult::kFailed;
    }
    scan = scan_owned(vm, quiescing_action, [&deadline](const Entry& entry) noexcept {
      if (entry.deadline < deadline) {
        deadline = entry.deadline;
      }
    });
  }
  if (!scan.complete()) {
    return quiesce_result(scan.verdict);
  }
  if (scan.count == 0) {
    return QuiesceResult::kComplete;
  }
  for (const dma::DeviceId device_id : scan.devices()) {
    if (!backend_drained(device_id)) {
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
  ScanOutcome scan{};
  {
    sync::Guard guard{g_lock};
    if (!g_registry_valid || generation == 0U || g_registry.owner_failed(vm)) {
      return false;
    }
    if (g_registry.owner_active(vm, generation)) {
      return true;
    }
    scan = scan_owned(
        vm, [](const Entry& entry) noexcept { return classify_resume(entry.state); },
        [](Entry& entry) noexcept { entry.state = State::kResuming; });
  }
  // Resume never defers: a stopped walk means some entry is not quiesced.
  if (!scan.complete()) {
    return false;
  }
  if (scan.count == 0) {
    return true;
  }

  static_cast<void>(smmu::poll_events());
  if (!smmu::attach_vm(vm, generation)) {
    fail_vm(vm, "stream attach");
    return false;
  }
  for (const dma::DeviceId device_id : scan.devices()) {
    if (!prepare_interrupt(device_id, vm)) {
      fail_vm(vm, "interrupt prepare");
      return false;
    }
    if (!backend_resume(device_id)) {
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
  for (const dma::DeviceId device_id : scan.devices()) {
    if (const dma::DeviceInterrupt* interrupt = interrupt_for_device(device_id);
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
  const bool   ok = g_registry_valid && entry != nullptr && entry->owner_vm == vm && entry->state == State::kActive &&
                  generation != 0U && entry->generation == generation &&
                  backend_start(device_id, source, destination, count, to_ram);
  // A transaction leaving the device for the SMMU. The address is the
  // one the device is given, which is the IPA the SMMU translates.
  if (ok) {
    trace_emit(NOVA_TRACE_EV_DMA_START, static_cast<std::uint32_t>(vm), to_ram ? source : destination, count);
  }
  return ok;
}

} // namespace nova::dma_device

namespace nova {

void dma_device_component::handle_irq(IrqCall* call) noexcept {
  if (call->handled) {
    return;
  }
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
  if (!vgic::post_spi_tracked(vm, interrupt->virtual_intid, interrupt->physical_intid, generation)) {
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

// The DMA half of VM power. Claiming every request tells the caller a
// device stack is present; the per-op answers are the same ones VM
// lifecycle used to call directly.
void dma_device_component::handle_quiesce(DmaQuiesceCall* call) noexcept {
  call->handled = true;
  switch (call->op) {
  case DmaQuiesceOp::kBegin:
    call->result = dma_device::begin_quiesce(call->vm);
    return;
  case DmaQuiesceOp::kPoll:
    call->result = dma_device::poll_quiesce(call->vm);
    return;
  case DmaQuiesceOp::kResume:
    call->result =
        dma_device::resume_vm(call->vm, call->generation) ? DmaQuiesceResult::kComplete : DmaQuiesceResult::kFailed;
    return;
  case DmaQuiesceOp::kCanStart:
    call->result = dma_device::can_start(call->vm) ? DmaQuiesceResult::kComplete : DmaQuiesceResult::kFailed;
    return;
  }
}

} // namespace nova
