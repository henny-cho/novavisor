#include "dma_probe/dma_probe.hpp"

#include "hal/console.hpp"
#include "hal/dma_probe.hpp"
#include "hal/timer.hpp"
#include "nova/abi/guest.hpp"
#include "nova_panic/nova_panic.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <string_view>

namespace nova::dma_probe {
namespace {

using namespace std::literals;

inline constexpr std::size_t   kOwnerVm       = 0;
inline constexpr std::uint64_t kTransferBytes = sizeof(std::uint64_t);
inline constexpr std::uint64_t kTimeoutMs     = 2'000;
inline constexpr std::uint64_t kSentinelMask  = 0xA5A5'5A5A'3C3C'C3C3ULL;

[[nodiscard]] auto deadline_after_ms(std::uint64_t milliseconds) noexcept -> std::uint64_t {
  return hyp_timer::now() + (hyp_timer::freq() / 1000U) * milliseconds;
}

[[nodiscard]] auto wait_idle() noexcept -> bool {
  const std::uint64_t deadline = deadline_after_ms(kTimeoutMs);
  while (hw::device::dma_running()) {
    if (hyp_timer::now() >= deadline) {
      return false;
    }
  }
  hw::device::acquire_memory();
  return true;
}

[[nodiscard]] auto wait_for_fault() noexcept -> bool {
  const std::uint64_t deadline = deadline_after_ms(kTimeoutMs);
  for (;;) {
    if (smmu::poll_events() != 0U) {
      return true;
    }
    if (hyp_timer::now() >= deadline) {
      return false;
    }
  }
}

[[nodiscard]] auto transfer(std::uint64_t source, std::uint64_t destination, bool to_ram) noexcept -> bool {
  return hw::device::start_dma(source, destination, kTransferBytes, to_ram) && wait_idle();
}

struct ProbeResources {
  volatile std::uint64_t* scratch           = nullptr;
  std::uint64_t           original          = 0;
  bool                    device_configured = false;
};

[[nodiscard]] auto release(ProbeResources& resources) noexcept -> bool {
  bool idle = true;
  if (resources.device_configured) {
    hw::device::disable_bus_master();
    idle = wait_idle();
  }
  if (idle && resources.scratch != nullptr) {
    *resources.scratch = resources.original;
    hw::device::publish_memory();
    resources.scratch = nullptr;
  }
  return idle;
}

[[noreturn]] void fail(std::string_view reason, ProbeResources& resources) noexcept {
  static_cast<void>(release(resources));
  console::write_parts(std::array{"[NOVA PANIC] DMA isolation probe failed: "sv, reason, "\n"sv});
  halt();
}

} // namespace

void run() noexcept {
  if (!hw::device::present()) {
    return;
  }

  ProbeResources resources{};
  const auto     guests = guest_table();
  if (guests.empty()) {
    fail("guest configuration", resources);
  }
  resources.device_configured = true;
  if (!hw::device::configure_bar()) {
    fail("PCI configuration", resources);
  }
  const GuestDescriptor& guest       = guests[kOwnerVm];
  const std::uint64_t    source_ipa  = guest.entry_pc;
  const std::uint64_t    scratch_ipa = guest.dtb_ipa - kTransferBytes;
  if (guest.dtb_ipa < guest.ipa_base + kTransferBytes || !guest.contains(source_ipa, kTransferBytes) ||
      !guest.contains(scratch_ipa, kTransferBytes) || !smmu::attach_vm(kOwnerVm, vcpu::vm_generation(kOwnerVm))) {
    fail("stream attach", resources);
  }

  auto* const         source   = reinterpret_cast<volatile std::uint64_t*>(guest.to_pa(source_ipa));
  auto* const         scratch  = reinterpret_cast<volatile std::uint64_t*>(guest.to_pa(scratch_ipa));
  const std::uint64_t expected = *source;
  const std::uint64_t original = *scratch;
  const std::uint64_t sentinel = expected ^ kSentinelMask;
  resources.scratch            = scratch;
  resources.original           = original;

  *scratch = sentinel;
  hw::device::publish_memory();
  hw::device::enable_bus_master();

  if (!transfer(source_ipa, hw::device::kInternalBuffer, false) ||
      !transfer(hw::device::kInternalBuffer, scratch_ipa, true)) {
    fail("round-trip timeout", resources);
  }
  if (*scratch != expected) {
    fail("round-trip mismatch", resources);
  }
  console::write("[dma] EDU round-trip ok\n");

  *scratch = sentinel;
  hw::device::publish_memory();
  if (!transfer(hw::device::kInternalBuffer, guest.ipa_base + guest.ipa_size, true)) {
    fail("fault request timeout", resources);
  }
  if (!wait_for_fault()) {
    fail("missing SMMU fault", resources);
  }

  if (!transfer(hw::device::kInternalBuffer, scratch_ipa, true)) {
    fail("quarantine retry timeout", resources);
  }
  hw::device::acquire_memory();
  if (*scratch != sentinel) {
    fail("quarantine bypass", resources);
  }

  if (!release(resources)) {
    fail("device quiesce", resources);
  }
  static_cast<void>(smmu::poll_events());
  console::write("[dma] EDU isolation probe passed\n");
}

} // namespace nova::dma_probe
