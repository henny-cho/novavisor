#pragma once

// Static DMA ownership shared by board policy and the SMMU component.

#include "nova/abi/guest.hpp"

#include <cstddef>
#include <cstdint>
#include <limits>
#include <span>

namespace nova::dma {

using DeviceId = std::uint16_t;

inline constexpr std::size_t   kNoVm            = std::numeric_limits<std::size_t>::max();
inline constexpr DeviceId      kNoDevice        = std::numeric_limits<DeviceId>::max();
inline constexpr std::uint32_t kFaultAuditBurst = 4;
inline constexpr std::uint64_t kPageSize        = 4096;

struct Assignment {
  DeviceId      device_id = kNoDevice;
  std::uint32_t stream_id = 0;
  std::size_t   vm        = kNoVm;
};

struct DeviceStream {
  DeviceId      device_id = kNoDevice;
  std::uint32_t stream_id = 0;
};

struct DeviceRegion {
  DeviceId      device_id = kNoDevice;
  std::uint64_t ipa_base  = 0;
  std::uint64_t pa_base   = 0;
  std::uint64_t size      = 0;
};

enum class InterruptTrigger : std::uint8_t {
  kLevel,
  kEdge,
};

enum class ResetCapability : std::uint8_t {
  kNone,
  kQuiesce,
  kFunction,
};

struct DeviceCapability {
  DeviceId        device_id = kNoDevice;
  ResetCapability reset     = ResetCapability::kNone;
  bool            coherent  = false;
};

struct DeviceInterrupt {
  DeviceId         device_id      = kNoDevice;
  std::uint32_t    physical_intid = 0;
  std::uint32_t    virtual_intid  = 0;
  InterruptTrigger trigger        = InterruptTrigger::kLevel;
};

// Defined by the active project. An empty table keeps every stream
// blocked while still initializing guest-owned translation contexts.
auto assignment_table() noexcept -> std::span<const Assignment>;

// Defined by the active project from the selected board's device
// inventory. Every assigned device must use exactly these streams.
auto device_stream_table() noexcept -> std::span<const DeviceStream>;
auto device_region_table() noexcept -> std::span<const DeviceRegion>;
auto device_interrupt_table() noexcept -> std::span<const DeviceInterrupt>;
auto device_capability_table() noexcept -> std::span<const DeviceCapability>;

struct PhysicalRange {
  std::uint64_t base = 0;
  std::uint64_t size = 0;
};

struct PolicyLimits {
  std::uint8_t                   sid_bits = 0;
  std::span<const PhysicalRange> protected_pa{};
};

enum class PolicyError : std::uint8_t {
  kNone,
  kInvalidCapabilities,
  kInvalidGuestWindow,
  kOverlappingGuestPa,
  kProtectedPaOverlap,
  kInvalidOwner,
  kInvalidDevice,
  kStreamIdOutOfRange,
  kDuplicateStream,
  kConflictingDeviceOwner,
  kUnknownDeviceStream,
  kIncompleteDeviceAssignment,
};

struct PolicyCheck {
  PolicyError error         = PolicyError::kNone;
  std::size_t index         = 0;
  std::size_t related_index = kNoVm;

  [[nodiscard]] constexpr auto ok() const noexcept -> bool { return error == PolicyError::kNone; }
};

enum class AccessResult : std::uint8_t {
  kAllow,
  kUnassignedStream,
  kOutsideGuestWindow,
  kInvalidPolicy,
};

enum class FaultAction : std::uint8_t {
  kNone,
  kBlockAndAudit,
  kQuarantineAndResetVm,
  kHaltHypervisor,
};

struct AccessDecision {
  AccessResult  result = AccessResult::kInvalidPolicy;
  FaultAction   action = FaultAction::kHaltHypervisor;
  std::size_t   vm     = kNoVm;
  std::uint64_t pa     = 0;

  [[nodiscard]] constexpr auto allowed() const noexcept -> bool { return result == AccessResult::kAllow; }
};

[[nodiscard]] constexpr auto owner_of(std::span<const Assignment> assignments, DeviceId device_id) noexcept
    -> std::size_t {
  std::size_t owner = kNoVm;
  for (const Assignment& assignment : assignments) {
    if (assignment.device_id != device_id) {
      continue;
    }
    if (owner != kNoVm && owner != assignment.vm) {
      return kNoVm;
    }
    owner = assignment.vm;
  }
  return owner;
}

[[nodiscard]] constexpr auto range_well_formed(std::uint64_t base, std::uint64_t size) noexcept -> bool {
  return size != 0 && base <= std::numeric_limits<std::uint64_t>::max() - (size - 1U);
}

[[nodiscard]] constexpr auto guest_range_valid(std::uint64_t base, std::uint64_t size) noexcept -> bool {
  return range_well_formed(base, size) && (base % kPageSize) == 0 && (size % kPageSize) == 0;
}

[[nodiscard]] constexpr auto ranges_overlap(std::uint64_t lhs_base, std::uint64_t lhs_size, std::uint64_t rhs_base,
                                            std::uint64_t rhs_size) noexcept -> bool {
  if (lhs_size == 0 || rhs_size == 0) {
    return false;
  }
  return lhs_base <= rhs_base ? rhs_base - lhs_base < lhs_size : lhs_base - rhs_base < rhs_size;
}

[[nodiscard]] constexpr auto validate_policy(std::span<const Assignment>      assignments,
                                             std::span<const GuestDescriptor> guests, PolicyLimits limits) noexcept
    -> PolicyCheck {
  if (limits.sid_bits == 0 || limits.sid_bits > std::numeric_limits<std::uint32_t>::digits) {
    return {.error = PolicyError::kInvalidCapabilities};
  }
  for (std::size_t i = 0; i < limits.protected_pa.size(); ++i) {
    if (!range_well_formed(limits.protected_pa[i].base, limits.protected_pa[i].size)) {
      return {.error = PolicyError::kInvalidCapabilities, .index = i};
    }
  }

  for (std::size_t i = 0; i < guests.size(); ++i) {
    if (!guest_range_valid(guests[i].ipa_base, guests[i].ipa_size) ||
        !guest_range_valid(guests[i].load_pa, guests[i].ipa_size)) {
      return {.error = PolicyError::kInvalidGuestWindow, .index = i};
    }
    for (std::size_t j = 0; j < limits.protected_pa.size(); ++j) {
      if (ranges_overlap(guests[i].load_pa, guests[i].ipa_size, limits.protected_pa[j].base,
                         limits.protected_pa[j].size)) {
        return {.error = PolicyError::kProtectedPaOverlap, .index = i, .related_index = j};
      }
    }
    for (std::size_t j = 0; j < i; ++j) {
      if (ranges_overlap(guests[i].load_pa, guests[i].ipa_size, guests[j].load_pa, guests[j].ipa_size)) {
        return {.error = PolicyError::kOverlappingGuestPa, .index = i, .related_index = j};
      }
    }
  }

  for (std::size_t i = 0; i < assignments.size(); ++i) {
    if (assignments[i].vm >= guests.size()) {
      return {.error = PolicyError::kInvalidOwner, .index = i};
    }
    if (assignments[i].device_id == kNoDevice) {
      return {.error = PolicyError::kInvalidDevice, .index = i};
    }
    if (limits.sid_bits < std::numeric_limits<std::uint32_t>::digits &&
        assignments[i].stream_id >= (std::uint32_t{1} << limits.sid_bits)) {
      return {.error = PolicyError::kStreamIdOutOfRange, .index = i};
    }
    for (std::size_t j = 0; j < i; ++j) {
      if (assignments[i].stream_id == assignments[j].stream_id) {
        return {.error = PolicyError::kDuplicateStream, .index = i, .related_index = j};
      }
      if (assignments[i].device_id == assignments[j].device_id && assignments[i].vm != assignments[j].vm) {
        return {.error = PolicyError::kConflictingDeviceOwner, .index = i, .related_index = j};
      }
    }
  }
  return {};
}

[[nodiscard]] constexpr auto validate_device_policy(std::span<const Assignment>   assignments,
                                                    std::span<const DeviceStream> devices,
                                                    std::uint8_t                  sid_bits) noexcept -> PolicyCheck {
  if (sid_bits == 0 || sid_bits > std::numeric_limits<std::uint32_t>::digits) {
    return {.error = PolicyError::kInvalidCapabilities};
  }
  for (std::size_t i = 0; i < devices.size(); ++i) {
    if (devices[i].device_id == kNoDevice) {
      return {.error = PolicyError::kInvalidDevice, .index = i};
    }
    if (sid_bits < std::numeric_limits<std::uint32_t>::digits &&
        devices[i].stream_id >= (std::uint32_t{1} << sid_bits)) {
      return {.error = PolicyError::kStreamIdOutOfRange, .index = i};
    }
    for (std::size_t j = 0; j < i; ++j) {
      if (devices[i].stream_id == devices[j].stream_id) {
        return {.error = PolicyError::kDuplicateStream, .index = i, .related_index = j};
      }
    }
  }

  for (std::size_t i = 0; i < assignments.size(); ++i) {
    bool found = false;
    for (const DeviceStream& device : devices) {
      found = found || (assignments[i].device_id == device.device_id && assignments[i].stream_id == device.stream_id);
    }
    if (!found) {
      return {.error = PolicyError::kUnknownDeviceStream, .index = i};
    }
  }

  for (std::size_t i = 0; i < devices.size(); ++i) {
    bool device_assigned = false;
    bool stream_assigned = false;
    for (const Assignment& assignment : assignments) {
      if (assignment.device_id == devices[i].device_id) {
        device_assigned = true;
        stream_assigned = stream_assigned || assignment.stream_id == devices[i].stream_id;
      }
    }
    if (device_assigned && !stream_assigned) {
      return {.error = PolicyError::kIncompleteDeviceAssignment, .index = i};
    }
  }
  return {};
}

[[nodiscard]] constexpr auto validate_policy(std::span<const Assignment>      assignments,
                                             std::span<const DeviceStream>    devices,
                                             std::span<const GuestDescriptor> guests, PolicyLimits limits) noexcept
    -> PolicyCheck {
  const PolicyCheck ownership = validate_policy(assignments, guests, limits);
  return ownership.ok() ? validate_device_policy(assignments, devices, limits.sid_bits) : ownership;
}

[[nodiscard]] constexpr auto decide_access(std::span<const Assignment>      assignments,
                                           std::span<const GuestDescriptor> guests, std::uint32_t stream_id,
                                           std::uint64_t iova, std::uint64_t size) noexcept -> AccessDecision {
  const Assignment* assignment = nullptr;
  for (const Assignment& candidate : assignments) {
    if (candidate.stream_id == stream_id) {
      if (assignment != nullptr) {
        return {};
      }
      assignment = &candidate;
    }
  }
  if (assignment == nullptr) {
    return {.result = AccessResult::kUnassignedStream, .action = FaultAction::kBlockAndAudit};
  }
  if (assignment->vm >= guests.size()) {
    return {};
  }

  const GuestDescriptor& guest = guests[assignment->vm];
  if (!guest_range_valid(guest.ipa_base, guest.ipa_size) || !guest_range_valid(guest.load_pa, guest.ipa_size)) {
    return {};
  }
  if (size == 0 || size > guest.ipa_size || iova < guest.ipa_base || iova - guest.ipa_base > guest.ipa_size - size) {
    return {.result = AccessResult::kOutsideGuestWindow,
            .action = FaultAction::kQuarantineAndResetVm,
            .vm     = assignment->vm};
  }

  return {.result = AccessResult::kAllow,
          .action = FaultAction::kNone,
          .vm     = assignment->vm,
          .pa     = guest.load_pa + (iova - guest.ipa_base)};
}

} // namespace nova::dma
