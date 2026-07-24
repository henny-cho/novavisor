#include "dma_probe/dma_probe.hpp"

#include "dma_device/dma_device.hpp"
#include "hal/console.hpp"
#include "hal/dma_device.hpp"
#include "hal/timer.hpp"
#include "nova/abi/guest.hpp"
#include "nova/abi/hvc_abi.h"
#include "nova/arch/trap_context.hpp"
#include "nova_panic/nova_panic.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <string_view>

namespace nova::dma_probe {
namespace {

using namespace std::literals;

inline constexpr std::uint64_t kTransferBytes = sizeof(std::uint64_t);
inline constexpr std::uint64_t kTimeoutMs     = 2'000;
inline constexpr std::uint64_t kSentinelMask  = 0xA5A5'5A5A'3C3C'C3C3ULL;

namespace device = dma_device::hw::device;

[[nodiscard]] auto assignment() noexcept -> const dma::Assignment* {
  for (const dma::Assignment& candidate : dma::assignment_table()) {
    if (candidate.device_id == device::kDmaDeviceId) {
      return &candidate;
    }
  }
  return nullptr;
}

[[nodiscard]] auto deadline_after_ms(std::uint64_t milliseconds) noexcept -> std::uint64_t {
  return hyp_timer::now() + (hyp_timer::freq() / 1000U) * milliseconds;
}

[[nodiscard]] auto wait_idle() noexcept -> bool {
  const std::uint64_t deadline = deadline_after_ms(kTimeoutMs);
  while (device::dma_running()) {
    if (hyp_timer::now() >= deadline) {
      return false;
    }
  }
  device::acquire_memory();
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

[[nodiscard]] auto transfer(std::size_t vm, std::uint64_t generation, std::uint64_t source, std::uint64_t destination,
                            bool to_ram) noexcept -> bool {
  return dma_device::start_dma(device::kDmaDeviceId, vm, generation, source, destination, kTransferBytes, to_ram) &&
         wait_idle();
}

struct ProbeResources {
  volatile std::uint64_t* scratch           = nullptr;
  std::uint64_t           original          = 0;
  std::size_t             owner_vm          = dma::kNoVm;
  bool                    device_configured = false;
};

[[nodiscard]] auto release(ProbeResources& resources) noexcept -> bool {
  bool quiesced = true;
  if (resources.device_configured) {
    auto result = dma_device::begin_quiesce(resources.owner_vm);
    while (result == dma_device::QuiesceResult::kPending) {
      result = dma_device::poll_quiesce(resources.owner_vm);
    }
    quiesced = result == dma_device::QuiesceResult::kComplete;
  }
  if (quiesced && resources.scratch != nullptr) {
    *resources.scratch = resources.original;
    device::publish_memory();
    resources.scratch = nullptr;
  }
  return quiesced;
}

[[noreturn]] void fail(std::string_view reason, ProbeResources& resources) noexcept {
  static_cast<void>(release(resources));
  console::write_parts(std::array{"[NOVA PANIC] DMA isolation probe failed: "sv, reason, "\n"sv});
  halt();
}

} // namespace

void run() noexcept {
  if (!device::present()) {
    return;
  }

  const dma::Assignment* device_assignment = assignment();
  if (device_assignment == nullptr) {
    return;
  }
  ProbeResources resources{};
  const auto     guests = guest_table();
  if (device_assignment->vm >= guests.size()) {
    fail("guest configuration", resources);
  }
  resources.device_configured        = true;
  resources.owner_vm                 = device_assignment->vm;
  const GuestDescriptor& guest       = guests[resources.owner_vm];
  const std::uint64_t    generation  = vcpu::vm_generation(resources.owner_vm);
  const std::uint64_t    source_ipa  = guest.entry_pc;
  const std::uint64_t    scratch_ipa = guest.dtb_ipa - kTransferBytes;
  if (guest.dtb_ipa < guest.ipa_base + kTransferBytes || !guest.contains(source_ipa, kTransferBytes) ||
      !guest.contains(scratch_ipa, kTransferBytes) || !dma_device::resume_vm(resources.owner_vm, generation)) {
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
  device::publish_memory();

  if (!transfer(resources.owner_vm, generation, source_ipa, device::kInternalBuffer, false) ||
      !transfer(resources.owner_vm, generation, device::kInternalBuffer, scratch_ipa, true)) {
    fail("round-trip timeout", resources);
  }
  if (*scratch != expected) {
    fail("round-trip mismatch", resources);
  }
  console::write("[dma] EDU round-trip ok\n");

  *scratch = sentinel;
  device::publish_memory();
  if (!transfer(resources.owner_vm, generation, device::kInternalBuffer, guest.ipa_base + guest.ipa_size, true)) {
    fail("fault request timeout", resources);
  }
  if (!wait_for_fault()) {
    fail("missing SMMU fault", resources);
  }

  if (!transfer(resources.owner_vm, generation, device::kInternalBuffer, scratch_ipa, true)) {
    fail("quarantine retry timeout", resources);
  }
  device::acquire_memory();
  if (*scratch != sentinel) {
    fail("quarantine bypass", resources);
  }

  if (!release(resources)) {
    fail("device quiesce", resources);
  }
  static_cast<void>(smmu::poll_events());
  console::write("[dma] EDU isolation probe passed\n");
  const std::uint64_t recovered_generation = vcpu::renew_preboot_generation(resources.owner_vm);
  if (recovered_generation == 0U || !dma_device::resume_vm(resources.owner_vm, recovered_generation)) {
    fail("guest activation", resources);
  }
}

auto inject_runtime_fault(std::size_t vm, std::uint64_t generation) noexcept -> bool {
  const auto guests = guest_table();
  if (vm >= guests.size() || !device::present()) {
    return false;
  }
  const GuestDescriptor& guest = guests[vm];
  if (!dma_device::start_dma(device::kDmaDeviceId, vm, generation, device::kInternalBuffer,
                             guest.ipa_base + guest.ipa_size, kTransferBytes, true)) {
    return false;
  }
  console::write("[dma] runtime fault requested\n");
  return true;
}

} // namespace nova::dma_probe

namespace nova {

void dma_probe_component::handle_hvc(HvcCall* call) noexcept {
  if (call->func_id != NOVA_HVC_FN_DMA_FAULT_INJECT) {
    return;
  }
  call->handled         = true;
  const std::size_t vm  = vm_of(vcpu::current_index());
  const auto        gen = vcpu::vm_generation(vm);
  call->ctx->x[0]       = dma_probe::inject_runtime_fault(vm, gen) ? 0 : kSmcccNotSupported;
}

} // namespace nova
