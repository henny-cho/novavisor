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

#include <array>
#include <cstddef>
#include <cstdint>
#include <span>

namespace nova {

// Compile-time upper bound on guest_table() entries — sizes the static
// per-VM backing stores (Stage 2 table sets, restart budget).
inline constexpr std::size_t kMaxGuests = 4;

// VCPUs per VM, fixed stride. A flat "vCPU slot" identifies one
// execution context machine-wide; per-VM state (Stage 2, budget,
// watchdog) keys on vm_of(slot), everything else on the slot itself.
inline constexpr std::size_t kMaxVcpusPerVm = 2;
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

  // True when [ipa, ipa + len) lies fully inside the guest window.
  // len must not exceed ipa_size (callers clamp first).
  [[nodiscard]] constexpr auto contains(std::uint64_t ipa, std::uint64_t len) const noexcept -> bool {
    return ipa >= ipa_base && ipa <= ipa_base + ipa_size - len;
  }

  // Translate a window IPA to the backing PA (EL2 runs with a flat view
  // of physical memory). Valid only for addresses inside the window.
  [[nodiscard]] constexpr auto to_pa(std::uint64_t ipa) const noexcept -> std::uint64_t {
    return ipa - ipa_base + load_pa;
  }
};

// Defined by the active project (projects/*/guest_config.cpp). Never
// empty, at most kMaxGuests entries; entry [0] is the boot guest
// (vcpu 0 on core 0), further entries start off and are launched via
// HVC_VM_START. Only vcpu 0 boots with a VM; secondary vCPUs stay off
// until the guest brings them up through PSCI CPU_ON.
auto guest_table() noexcept -> std::span<const GuestDescriptor>;

} // namespace nova
