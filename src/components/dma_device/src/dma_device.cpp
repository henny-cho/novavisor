#include "dma_device/dma_device.hpp"

#include "hal/console.hpp"
#include "hal/dma_device.hpp"
#include "hal/timer.hpp"
#include "nova/abi/guest.hpp"
#include "nova/sync.hpp"

#include <cstddef>
#include <cstdint>
#include <string_view>

namespace nova::dma_device {
namespace {

inline constexpr std::size_t   kOwnerVm   = 0;
inline constexpr std::uint64_t kTimeoutMs = 2'000;

enum class State : std::uint8_t {
  kUnavailable,
  kQuiesced,
  kQuiescing,
  kResuming,
  kActive,
  kFailed,
};

sync::SpinLock g_lock;
State          g_state      = State::kUnavailable;
std::uint64_t  g_generation = 0;
std::uint64_t  g_deadline   = 0;

[[nodiscard]] auto deadline_after_ms(std::uint64_t milliseconds) noexcept -> std::uint64_t {
  return hyp_timer::now() + (hyp_timer::freq() / 1000U) * milliseconds;
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

void mark_failed(std::size_t vm, const char* reason) noexcept {
  static_cast<void>(hw::device::disable_bus_master());
  static_cast<void>(smmu::quarantine_vm(vm));
  {
    sync::Guard guard{g_lock};
    g_state = State::kFailed;
  }
  console::write("[dma] VM ");
  console::write_dec64(vm);
  console::write(" isolated: ");
  console::write(reason);
  console::write("\n");
}

[[nodiscard]] auto complete_quiesce(std::size_t vm) noexcept -> QuiesceResult {
  hw::device::acquire_memory();
  if (!smmu::detach_vm(vm)) {
    mark_failed(vm, "stream detach");
    return QuiesceResult::kFailed;
  }
  static_cast<void>(smmu::poll_events());
  {
    sync::Guard guard{g_lock};
    if (g_state != State::kQuiescing) {
      return g_state == State::kQuiesced ? QuiesceResult::kComplete : QuiesceResult::kFailed;
    }
    g_state = State::kQuiesced;
  }
  log_state(vm, "quiesced");
  return QuiesceResult::kComplete;
}

} // namespace

void init() noexcept {
  sync::Guard guard{g_lock};
  g_state      = State::kUnavailable;
  g_generation = 0;
  if (!hw::device::present()) {
    return;
  }
  if (guest_table().size() <= kOwnerVm || !hw::device::configure_bar()) {
    console::write("[dma] device configuration failed\n");
    g_state = State::kFailed;
    return;
  }
  g_state = State::kQuiesced;
}

auto begin_quiesce(std::size_t vm) noexcept -> QuiesceResult {
  {
    sync::Guard guard{g_lock};
    if (vm != kOwnerVm || g_state == State::kUnavailable || g_state == State::kQuiesced) {
      return QuiesceResult::kComplete;
    }
    if (g_state == State::kFailed) {
      return QuiesceResult::kFailed;
    }
    if (g_state == State::kQuiescing) {
      return QuiesceResult::kPending;
    }
    if (g_state != State::kActive) {
      return QuiesceResult::kFailed;
    }
    g_state    = State::kQuiescing;
    g_deadline = deadline_after_ms(kTimeoutMs);
  }

  if (!hw::device::disable_bus_master()) {
    mark_failed(vm, "bus-master disable");
    return QuiesceResult::kFailed;
  }
  if (hw::device::dma_running()) {
    return QuiesceResult::kPending;
  }
  return complete_quiesce(vm);
}

auto poll_quiesce(std::size_t vm) noexcept -> QuiesceResult {
  std::uint64_t deadline = 0;
  {
    sync::Guard guard{g_lock};
    if (vm != kOwnerVm || g_state == State::kUnavailable || g_state == State::kQuiesced) {
      return QuiesceResult::kComplete;
    }
    if (g_state == State::kFailed) {
      return QuiesceResult::kFailed;
    }
    if (g_state != State::kQuiescing) {
      return QuiesceResult::kFailed;
    }
    deadline = g_deadline;
  }

  if (!hw::device::dma_running()) {
    return complete_quiesce(vm);
  }
  if (hyp_timer::now() < deadline) {
    return QuiesceResult::kPending;
  }
  mark_failed(vm, "device drain timeout");
  return QuiesceResult::kFailed;
}

auto resume_vm(std::size_t vm, std::uint64_t generation) noexcept -> bool {
  {
    sync::Guard guard{g_lock};
    if (vm != kOwnerVm || g_state == State::kUnavailable) {
      return true;
    }
    if (g_state == State::kFailed || generation == 0U) {
      return false;
    }
    if (g_state == State::kActive) {
      return g_generation == generation;
    }
    if (g_state != State::kQuiesced) {
      return false;
    }
    g_state = State::kResuming;
  }

  static_cast<void>(smmu::poll_events());
  if (!smmu::attach_vm(vm, generation)) {
    mark_failed(vm, "stream attach");
    return false;
  }

  if (!hw::device::enable_bus_master()) {
    mark_failed(vm, "bus-master enable");
    return false;
  }
  {
    sync::Guard guard{g_lock};
    g_generation = generation;
    g_state      = State::kActive;
  }
  log_state(vm, "resumed", generation);
  return true;
}

auto can_start(std::size_t vm) noexcept -> bool {
  sync::Guard guard{g_lock};
  return vm != kOwnerVm || g_state != State::kFailed;
}

} // namespace nova::dma_device
