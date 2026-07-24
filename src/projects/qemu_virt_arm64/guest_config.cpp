// projects/qemu_virt_arm64/guest_config.cpp
//
// Defines the nova::guest_table() contract (nova/abi/guest.hpp) for
// this target. The per-guest scalars (window size, vcpu count, uart)
// are parsed at boot from the DTB blobs yml2dtb embedded into the
// image; placement (PA slot, entry, stack, VMID, affinity) derives
// from the list index by the same rules the static table used. This
// TU is linked directly into novavisor.elf — it is the injection
// point that keeps core components free of projects/ includes.

#include "guest_config.hpp"

#include "device_policy.hpp"
#include "dtb_parser/fdt_model.hpp"
#include "hal/console.hpp"
#include "nova/abi/dma.hpp"
#include "nova/abi/guest.hpp"
#include "nova/abi/payload.hpp"
#include "nova_panic/nova_panic.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <span>

// One generated record per configured guest. The binary pointer is null
// when the external loader compatibility mode is selected.
extern "C" {
extern const nova::payload::Metadata g_guest_payloads[];
extern const std::uint32_t           g_guest_payload_count;
}

namespace nova {
namespace {

// Entry [0] boots automatically; the rest stay off until a guest
// issues HVC_VM_START. Static affinity by slot: VMs 0/1 boot on
// core 0 (single-core demos unchanged), VMs 2/3 on core 1; VM 0's
// optional second vCPU lands on the other core so the boot slot can
// run SMP via PSCI CPU_ON.
constexpr std::array<std::array<std::uint8_t, kMaxVcpusPerVm>, kMaxGuests> kAffinity{{{0, 1}, {0, 0}, {1, 0}, {1, 1}}};
static_assert(kAffinity[0][0] == 0, "the boot guest belongs to the primary core");

std::array<GuestDescriptor, kMaxGuests> g_table{};
std::size_t                             g_count = 0;

[[noreturn]] void panic_guest_config(std::size_t index) noexcept {
  console::write("[NOVA PANIC] embedded guest DTB ");
  console::write_dec64(index);
  console::write(" invalid\n");
  halt();
}

} // namespace

namespace qemu_virt {

void init_guest_table() noexcept {
  const std::size_t count = g_guest_payload_count;
  if (count == 0 || count > kMaxGuests) {
    panic_guest_config(count);
  }
  std::uint64_t load_pa = kGuestIpaBase; // packed PA cursor
  for (std::size_t i = 0; i < count; ++i) {
    const payload::Metadata&            payload = g_guest_payloads[i];
    const std::span<const std::uint8_t> blob{payload.dtb_start,
                                             static_cast<std::size_t>(payload.dtb_end - payload.dtb_start)};
    const fdt::GuestInfo                info = fdt::parse_guest(blob);
    // yml2dtb validated sizes and collisions at build time — a failure
    // here means the blob or the parser regressed, not the config.
    if (!info.ok || info.cpus > kMaxVcpusPerVm || info.mem_base != kGuestIpaBase || blob.size() > NOVA_GUEST_DTB_SIZE ||
        payload.load_pa != load_pa || payload.memory_size != info.mem_size || payload.entry < kGuestIpaBase ||
        payload.entry >= kGuestIpaBase + info.mem_size) {
      panic_guest_config(i);
    }
    g_table[i] = GuestDescriptor{
        .ipa_base         = kGuestIpaBase,
        .ipa_size         = info.mem_size,
        .load_pa          = payload.load_pa,
        .entry_pc         = payload.entry,
        .stack_top        = guest_dtb_ipa(info.mem_size),
        .vmid             = static_cast<std::uint16_t>(i + 1),
        .vcpus            = static_cast<std::uint8_t>(info.cpus),
        .cpu              = kAffinity[i],
        .uart             = info.has_uart ? UartKind::kVuart : UartKind::kNone,
        .auto_start       = info.autostart,
        .dtb              = blob.data(),
        .dtb_size         = static_cast<std::uint32_t>(blob.size()),
        .dtb_ipa          = guest_dtb_ipa(info.mem_size),
        .payload          = payload.image,
        .payload_size     = payload.image_size,
        .payload_checksum = payload.checksum,
    };
    // Config-driven core assignment overrides the index-derived table
    // (yml2dtb validated the core indices at build time).
    if (info.has_affinity) {
      for (std::size_t v = 0; v < info.cpus && v < kMaxVcpusPerVm; ++v) {
        g_table[i].cpu[v] = info.affinity[v];
      }
    }
    load_pa = align_up_pa(load_pa + info.mem_size);
  }
  g_count = count;
}

} // namespace qemu_virt

auto guest_table() noexcept -> std::span<const GuestDescriptor> {
  return {g_table.data(), g_count};
}

auto dma::assignment_table() noexcept -> std::span<const dma::Assignment> {
  return generated::kDmaAssignments;
}

auto dma::device_stream_table() noexcept -> std::span<const dma::DeviceStream> {
  return generated::kDeviceStreams;
}

auto dma::device_region_table() noexcept -> std::span<const dma::DeviceRegion> {
  return generated::kDeviceRegions;
}

auto dma::device_interrupt_table() noexcept -> std::span<const dma::DeviceInterrupt> {
  return generated::kDeviceInterrupts;
}

auto dma::device_capability_table() noexcept -> std::span<const dma::DeviceCapability> {
  return generated::kDeviceCapabilities;
}

} // namespace nova
