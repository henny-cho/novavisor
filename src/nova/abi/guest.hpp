#pragma once

// nova/abi/guest.hpp
//
// GuestDescriptor: static per-guest configuration, and the contract
// between the project composition (which defines the table) and the
// core components (which consume it).
//
// Components must never include projects/ headers — this header is the
// inverted dependency boundary: core_mmu/core_vcpu read guest_table(),
// and each project links exactly one TU that defines it
// (projects/*/guest_config.cpp).

#include "nova/abi/guest_layout.h"
#include "nova/range.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <span>

namespace nova {

// Guest PA windows pack at NOVA_GUEST_PA_ALIGN: window i+1 starts at
// the aligned end of window i. The live PA packing (projects/common)
// and the pristine snapshot layout (core_mmu) share this single rule.
[[nodiscard]] constexpr auto align_up_pa(std::uint64_t addr) noexcept -> std::uint64_t {
  return (addr + NOVA_GUEST_PA_ALIGN - 1) & ~static_cast<std::uint64_t>(NOVA_GUEST_PA_ALIGN - 1);
}

// Compile-time upper bound on guest_table() entries — sizes the static
// per-VM backing stores (Stage 2 table sets, restart budget).
inline constexpr std::size_t kMaxGuests = NOVA_MAX_GUESTS;

// VCPUs per VM, fixed stride. A flat "vCPU slot" identifies one
// execution context machine-wide; per-VM state (Stage 2, budget,
// watchdog) keys on vm_of(slot), everything else on the slot itself.
inline constexpr std::size_t kMaxVcpusPerVm = NOVA_MAX_VCPUS_PER_VM;
inline constexpr std::size_t kMaxVcpus      = kMaxGuests * kMaxVcpusPerVm;

[[nodiscard]] constexpr auto vm_of(std::size_t slot) noexcept -> std::size_t {
  return slot / kMaxVcpusPerVm;
}
[[nodiscard]] constexpr auto vcpu_of(std::size_t slot) noexcept -> std::size_t {
  return slot % kMaxVcpusPerVm;
}
[[nodiscard]] constexpr auto slot_of(std::size_t vm, std::size_t vcpu = 0) noexcept -> std::size_t {
  return vm * kMaxVcpusPerVm + vcpu;
}

// Declarative per-VM device policy: which UART (if any) the VM sees.
// Passthrough of the physical UART is a future kind — it needs a
// second Stage 2 device region and costs EL2 its own console.
enum class UartKind : std::uint8_t { kNone, kVuart };

struct GuestDescriptor {
  std::uint64_t ipa_base  = 0; // Stage 2 IPA window base (guest view; same for every guest)
  std::uint64_t ipa_size  = 0; // IPA window length in bytes
  std::uint64_t load_pa   = 0; // PA slot backing the window (Stage 2 output address)
  std::uint64_t entry_pc  = 0; // initial ELR_EL2 — EL1 entry point (IPA)
  std::uint64_t stack_top = 0; // initial SP_EL1 (IPA)
  std::uint16_t vmid      = 0; // VTTBR_EL2 VMID tag (0 is reserved — never valid here)
  std::uint8_t  vcpus     = 1; // execution contexts sharing this VM's Stage 2 window

  // Static affinity per vCPU: the physical core each one runs on. A
  // vCPU executes only there; all its state is owned by that core.
  std::array<std::uint8_t, kMaxVcpusPerVm> cpu{};

  UartKind uart = UartKind::kNone; // vuart claims the PL011 frame only when set

  // Boot vCPU 0 at machine start without a guest-issued HVC_VM_START.
  // Entry [0] always boots regardless (it is the machine's reason to
  // exist); this flag brings up the other VMs of a multi-OS config.
  bool auto_start = false;

  // Configuration blob (FDT) embedded in the hypervisor image. Copied
  // to dtb_ipa before the pristine snapshot (so warm reset restores it
  // with the image) and handed to the boot vCPU in x0 — the Linux boot
  // protocol shape. Secondary vCPUs keep the PSCI context_id contract.
  const std::uint8_t* dtb      = nullptr;
  std::uint32_t       dtb_size = 0;
  std::uint64_t       dtb_ipa  = 0;

  // Optional binary embedded in the hypervisor image. A zero size keeps
  // compatibility with an external loader.
  const std::uint8_t* payload          = nullptr;
  std::uint64_t       payload_size     = 0;
  std::uint32_t       payload_checksum = 0;

  // True when [ipa, ipa + len) lies fully inside the guest window. Any
  // len answers: one past the window is out of bounds however large the
  // request, so a guest cannot aim a buffer at hypervisor memory by
  // asking for more than the window holds.
  [[nodiscard]] constexpr auto contains(std::uint64_t ipa, std::uint64_t len) const noexcept -> bool {
    return range_contains(ipa_base, ipa_size, ipa, len);
  }

  // Translate a window IPA to the backing PA (EL2 runs with a flat view
  // of physical memory). Valid only for addresses inside the window.
  [[nodiscard]] constexpr auto to_pa(std::uint64_t ipa) const noexcept -> std::uint64_t {
    return ipa - ipa_base + load_pa;
  }
};

// The window bound, in every shape a caller reaches it by. This is the
// check that stops a guest from pointing an HVC_PUTS buffer at
// hypervisor memory and reading EL2 out through the UART.
static_assert(
    [] {
      const GuestDescriptor window{.ipa_base = 0x4000'0000, .ipa_size = 0x0010'0000};
      const std::uint64_t   base = window.ipa_base;
      const std::uint64_t   size = window.ipa_size;
      return window.contains(base, 16) && window.contains(base + size - 16, 16) &&
             window.contains(base + size - 1, 1) &&    // the exact last byte
             window.contains(base, size) &&            // the whole window, only from its base
             !window.contains(base + 1, size) &&       //
             !window.contains(base + size - 8, 16) &&  // straddling either end
             !window.contains(base - 8, 16) &&         //
             !window.contains(0, 16) &&                // the hypervisor's own low memory
             !window.contains(~std::uint64_t{0}, 1) && // no wrap into the window
             window.contains(base, 0) &&               // a zero-length buffer dereferences nothing, so
             window.contains(base + size, 0) &&        // it is in bounds up to one past the end
             !window.contains(base - 1, 0) &&          //
             !window.contains(base, size + 1) &&       // longer than the window: no address satisfies it
             !window.contains(base + 0x1000, size + 1);
    }(),
    "the guest window admits exactly the buffers that lie inside it");

// Total bytes the pristine snapshot area spans for `guests`, packed
// with the same align_up_pa rule as the live windows. Consumers: the
// core_mmu reservation check and the SMMU's DMA-protected range —
// both must see the exact layout the snapshot copies use.
[[nodiscard]] constexpr auto pristine_span(std::span<const GuestDescriptor> guests) noexcept -> std::uint64_t {
  std::uint64_t end = 0;
  for (std::size_t i = 0; i < guests.size(); ++i) {
    end += guests[i].ipa_size;
    if (i + 1 < guests.size()) {
      end = align_up_pa(end);
    }
  }
  return end;
}

// Defined by the active project (projects/*/guest_config.cpp). Never
// empty, at most kMaxGuests entries; entry [0] is the boot guest
// (vcpu 0 on core 0), further entries start off and are launched via
// HVC_VM_START. Only vcpu 0 boots with a VM; secondary vCPUs stay off
// until the guest brings them up through PSCI CPU_ON.
auto guest_table() noexcept -> std::span<const GuestDescriptor>;

} // namespace nova
