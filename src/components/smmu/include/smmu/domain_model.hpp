#pragma once

// Stream ownership and translation-context lifecycle.

#include "nova/abi/dma.hpp"
#include "nova/abi/guest.hpp"

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
    if ((context.root_pa & ~0x0000'00FF'FFFF'FFFFULL) != 0U) {
      return ContextError::kRootOutOfRange;
    }
    if (!dma::range_well_formed(guests[i].load_pa, guests[i].ipa_size) ||
        guests[i].load_pa + guests[i].ipa_size - 1U > 0x0000'00FF'FFFF'FFFFULL) {
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
