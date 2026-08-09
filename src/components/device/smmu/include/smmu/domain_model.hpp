#pragma once

// Stream ownership and translation-context lifecycle.

#include "nova/abi/dma.hpp"
#include "nova/abi/guest.hpp"
#include "nova/range.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <span>

namespace nova::smmu {

enum class DomainState : std::uint8_t {
  kDetached,
  kAttached,
  kQuarantined,
};

struct TranslationContext {
  std::size_t   owner_vm = dma::kNoVm;
  std::uint16_t vmid     = 0;
  std::uint64_t root_pa  = 0;
};

enum class ContextError : std::uint8_t {
  kNone,
  kInvalidOwner,
  kInvalidVmid,
  kDuplicateVmid,
  kUnalignedRoot,
  kRootOutOfRange,
  kGuestPaOutOfRange,
  kDuplicateRoot,
};

struct StreamBinding {
  std::size_t   owner_vm   = dma::kNoVm;
  std::uint64_t generation = 0;
  DomainState   state      = DomainState::kDetached;

  [[nodiscard]] constexpr auto configured() const noexcept -> bool { return owner_vm != dma::kNoVm; }
};

// Per-VM stream lists derived once from the configured bindings: owner
// assignment is fixed at build time while only state/generation mutate,
// so every domain operation walks its own streams instead of rescanning
// the whole stream table.
template <std::size_t MaxStreams>
struct StreamIndex {
  std::array<std::array<std::uint16_t, MaxStreams>, kMaxGuests> sids{};
  std::array<std::size_t, kMaxGuests>                           counts{};

  [[nodiscard]] constexpr auto streams_of(std::size_t vm) const noexcept -> std::span<const std::uint16_t> {
    if (vm >= kMaxGuests) {
      return {};
    }
    return std::span<const std::uint16_t>{sids[vm].data(), counts[vm]};
  }
};

// Unconfigured streams belong to no VM; an owner outside the guest table
// cannot be indexed and is skipped.
template <std::size_t MaxStreams>
[[nodiscard]] constexpr auto build_stream_index(std::span<const StreamBinding> bindings) noexcept
    -> StreamIndex<MaxStreams> {
  StreamIndex<MaxStreams> index{};
  for (std::size_t sid = 0; sid < bindings.size(); ++sid) {
    const std::size_t vm = bindings[sid].owner_vm;
    if (!bindings[sid].configured() || vm >= kMaxGuests || index.counts[vm] >= MaxStreams) {
      continue;
    }
    index.sids[vm][index.counts[vm]++] = static_cast<std::uint16_t>(sid);
  }
  return index;
}

// The notice itself is part of the DMA ABI (nova/abi/dma.hpp) so the
// recovery side can name it without depending on this component.
using FaultNotice = dma::FaultNotice;

template <std::size_t Capacity>
struct FaultNoticeBatch {
  std::array<FaultNotice, Capacity> notices{};
  std::size_t                       count = 0;
};

template <std::size_t Capacity, typename Quarantine>
[[nodiscard]] constexpr auto collect_fault_notices(std::span<const std::uint32_t> stream_ids,
                                                   Quarantine quarantine) noexcept -> FaultNoticeBatch<Capacity> {
  FaultNoticeBatch<Capacity> notices{};
  for (const std::uint32_t stream_id : stream_ids) {
    const FaultNotice notice = quarantine(stream_id);
    if (notice.valid() && notices.count < notices.notices.size()) {
      notices.notices[notices.count++] = notice;
    }
  }
  return notices;
}

[[nodiscard]] constexpr auto snapshot_fault(const StreamBinding& binding, std::uint32_t stream_id) noexcept
    -> FaultNotice {
  if (!binding.configured() || binding.state != DomainState::kAttached || binding.generation == 0U) {
    return {};
  }
  return {.owner_vm = binding.owner_vm, .stream_id = stream_id, .generation = binding.generation};
}

// Output address space the Stage-2 contexts are encoded for: 40 bits.
// A root table and a guest's whole PA window have to sit below it.
inline constexpr std::uint64_t kOutputAddressLimit = std::uint64_t{1} << 40U;

[[nodiscard]] constexpr auto validate_contexts(std::span<const TranslationContext> contexts,
                                               std::span<const GuestDescriptor> guests, bool vmid16) noexcept
    -> ContextError {
  if (contexts.size() != guests.size()) {
    return ContextError::kInvalidOwner;
  }
  for (std::size_t i = 0; i < contexts.size(); ++i) {
    const TranslationContext& context = contexts[i];
    if (context.owner_vm != i) {
      return ContextError::kInvalidOwner;
    }
    if (context.vmid == 0U || context.vmid != guests[i].vmid || (!vmid16 && context.vmid > 0xFFU)) {
      return ContextError::kInvalidVmid;
    }
    if ((context.root_pa & 0xFFFU) != 0U) {
      return ContextError::kUnalignedRoot;
    }
    if (context.root_pa >= kOutputAddressLimit) {
      return ContextError::kRootOutOfRange;
    }
    // Whether the window is well formed is the DMA policy's answer, given
    // before any context is built; what is left is that it fits.
    if (!range_contains(0, kOutputAddressLimit, guests[i].load_pa, guests[i].ipa_size)) {
      return ContextError::kGuestPaOutOfRange;
    }
    for (std::size_t j = 0; j < i; ++j) {
      if (context.vmid == contexts[j].vmid) {
        return ContextError::kDuplicateVmid;
      }
      if (context.root_pa == contexts[j].root_pa) {
        return ContextError::kDuplicateRoot;
      }
    }
  }
  return ContextError::kNone;
}

[[nodiscard]] constexpr auto configure_binding(StreamBinding& binding, std::size_t owner_vm,
                                               std::size_t guest_count) noexcept -> bool {
  if (owner_vm >= guest_count || binding.configured()) {
    return false;
  }
  binding.owner_vm = owner_vm;
  binding.state    = DomainState::kDetached;
  return true;
}

[[nodiscard]] constexpr auto can_attach(const StreamBinding& binding, std::uint64_t generation) noexcept -> bool {
  return binding.configured() && binding.state != DomainState::kAttached && generation > binding.generation;
}

[[nodiscard]] constexpr auto attachment_matches(const StreamBinding& binding, std::uint64_t generation) noexcept
    -> bool {
  return binding.configured() && binding.state == DomainState::kAttached && binding.generation == generation;
}

// True when every stream of the VM can move to (or already sits at) the
// requested generation — the all-or-nothing gate before any STE install.
[[nodiscard]] constexpr auto can_attach_vm(std::span<const StreamBinding> bindings, std::span<const std::uint16_t> sids,
                                           std::uint64_t generation) noexcept -> bool {
  for (const std::uint16_t sid : sids) {
    const StreamBinding& binding = bindings[sid];
    const bool           ok      = binding.state == DomainState::kAttached ? attachment_matches(binding, generation)
                                                                           : can_attach(binding, generation);
    if (!ok) {
      return false;
    }
  }
  return true;
}

[[nodiscard]] constexpr auto mark_attached(StreamBinding& binding, std::uint64_t generation) noexcept -> bool {
  if (!can_attach(binding, generation)) {
    return false;
  }
  binding.generation = generation;
  binding.state      = DomainState::kAttached;
  return true;
}

[[nodiscard]] constexpr auto mark_detached(StreamBinding& binding) noexcept -> bool {
  if (!binding.configured() || binding.state != DomainState::kAttached) {
    return false;
  }
  binding.state = DomainState::kDetached;
  return true;
}

[[nodiscard]] constexpr auto mark_quarantined(StreamBinding& binding) noexcept -> bool {
  if (!binding.configured()) {
    return false;
  }
  binding.state = DomainState::kQuarantined;
  return true;
}

} // namespace nova::smmu
