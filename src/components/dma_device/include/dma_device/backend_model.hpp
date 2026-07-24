#pragma once

#include "nova/abi/dma.hpp"

#include <cstddef>
#include <cstdint>
#include <span>

namespace nova::dma_device {

struct Backend {
  dma::DeviceId        device_id                                                = dma::kNoDevice;
  dma::ResetCapability reset_capability                                         = dma::ResetCapability::kNone;
  bool (*present)() noexcept                                                    = nullptr;
  bool (*configure)() noexcept                                                  = nullptr;
  bool (*quiesce)() noexcept                                                    = nullptr;
  bool (*drained)() noexcept                                                    = nullptr;
  bool (*reset)() noexcept                                                      = nullptr;
  bool (*resume)() noexcept                                                     = nullptr;
  bool (*start_dma)(std::uint64_t, std::uint64_t, std::uint64_t, bool) noexcept = nullptr;
  void (*clear_interrupts)() noexcept                                           = nullptr;
};

enum class BackendPolicyError : std::uint8_t {
  kNone,
  kCapacity,
  kInvalidBackend,
  kDuplicateBackend,
  kMissingCapability,
  kMissingBackend,
  kResetMismatch,
};

struct BackendPolicyCheck {
  BackendPolicyError error = BackendPolicyError::kNone;
  std::size_t        index = 0;

  [[nodiscard]] constexpr auto ok() const noexcept -> bool { return error == BackendPolicyError::kNone; }
};

[[nodiscard]] constexpr auto backend_well_formed(const Backend& backend) noexcept -> bool {
  const bool reset_valid = backend.reset_capability != dma::ResetCapability::kFunction || backend.reset != nullptr;
  return backend.device_id != dma::kNoDevice && backend.present != nullptr && backend.configure != nullptr &&
         backend.quiesce != nullptr && backend.drained != nullptr && reset_valid && backend.resume != nullptr &&
         backend.start_dma != nullptr && backend.clear_interrupts != nullptr;
}

[[nodiscard]] constexpr auto find_backend(std::span<const Backend> backends, dma::DeviceId device_id) noexcept
    -> const Backend* {
  for (const Backend& backend : backends) {
    if (backend.device_id == device_id) {
      return &backend;
    }
  }
  return nullptr;
}

[[nodiscard]] constexpr auto find_capability(std::span<const dma::DeviceCapability> capabilities,
                                             dma::DeviceId device_id) noexcept -> const dma::DeviceCapability* {
  for (const dma::DeviceCapability& capability : capabilities) {
    if (capability.device_id == device_id) {
      return &capability;
    }
  }
  return nullptr;
}

[[nodiscard]] constexpr auto validate_backend_policy(std::span<const dma::Assignment>       assignments,
                                                     std::span<const dma::DeviceCapability> capabilities,
                                                     std::span<const Backend> backends, std::size_t capacity) noexcept
    -> BackendPolicyCheck {
  if (capabilities.size() > capacity) {
    return {.error = BackendPolicyError::kCapacity};
  }
  for (std::size_t i = 0; i < backends.size(); ++i) {
    if (!backend_well_formed(backends[i])) {
      return {.error = BackendPolicyError::kInvalidBackend, .index = i};
    }
    for (std::size_t j = 0; j < i; ++j) {
      if (backends[i].device_id == backends[j].device_id) {
        return {.error = BackendPolicyError::kDuplicateBackend, .index = i};
      }
    }
  }
  for (std::size_t i = 0; i < assignments.size(); ++i) {
    const dma::DeviceCapability* capability = find_capability(capabilities, assignments[i].device_id);
    if (capability == nullptr) {
      return {.error = BackendPolicyError::kMissingCapability, .index = i};
    }
    const Backend* backend = find_backend(backends, assignments[i].device_id);
    if (backend == nullptr) {
      return {.error = BackendPolicyError::kMissingBackend, .index = i};
    }
    if (backend->reset_capability != capability->reset) {
      return {.error = BackendPolicyError::kResetMismatch, .index = i};
    }
  }
  return {};
}

} // namespace nova::dma_device
