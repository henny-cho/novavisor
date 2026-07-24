#pragma once

#include "nova/abi/dma.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <span>

namespace nova::dma_device {

enum class State : std::uint8_t {
  kUnavailable,
  kQuiesced,
  kQuiescing,
  kDetaching,
  kResuming,
  kActive,
  kFailed,
};

struct Entry {
  dma::DeviceId device_id          = dma::kNoDevice;
  std::size_t   owner_vm           = dma::kNoVm;
  State         state              = State::kUnavailable;
  std::uint64_t generation         = 0;
  std::uint64_t deadline           = 0;
  bool          bus_master_blocked = true;
};

template <std::size_t Capacity>
class Registry {
public:
  constexpr void reset() noexcept {
    entries_ = {};
    count_   = 0;
  }

  [[nodiscard]] constexpr auto add(dma::DeviceId device_id, std::size_t owner_vm) noexcept -> bool {
    for (std::size_t i = 0; i < count_; ++i) {
      if (entries_[i].device_id == device_id) {
        return entries_[i].owner_vm == owner_vm;
      }
    }
    if (device_id == dma::kNoDevice || owner_vm == dma::kNoVm || count_ == entries_.size()) {
      return false;
    }
    entries_[count_++] = {.device_id = device_id, .owner_vm = owner_vm};
    return true;
  }

  [[nodiscard]] constexpr auto find(dma::DeviceId device_id) noexcept -> Entry* {
    for (std::size_t i = 0; i < count_; ++i) {
      if (entries_[i].device_id == device_id) {
        return &entries_[i];
      }
    }
    return nullptr;
  }

  [[nodiscard]] constexpr auto find(dma::DeviceId device_id) const noexcept -> const Entry* {
    for (std::size_t i = 0; i < count_; ++i) {
      if (entries_[i].device_id == device_id) {
        return &entries_[i];
      }
    }
    return nullptr;
  }

  [[nodiscard]] constexpr auto entries() noexcept -> std::span<Entry> { return {entries_.data(), count_}; }

  [[nodiscard]] constexpr auto entries() const noexcept -> std::span<const Entry> { return {entries_.data(), count_}; }

  [[nodiscard]] constexpr auto owner_active(std::size_t vm, std::uint64_t generation) const noexcept -> bool {
    bool found = false;
    for (const Entry& entry : entries()) {
      if (entry.owner_vm != vm || entry.state == State::kUnavailable) {
        continue;
      }
      found = true;
      if (entry.state != State::kActive || generation == 0U || entry.generation != generation) {
        return false;
      }
    }
    return found;
  }

  [[nodiscard]] constexpr auto owner_failed(std::size_t vm) const noexcept -> bool {
    for (const Entry& entry : entries()) {
      if (entry.owner_vm == vm && entry.state == State::kFailed) {
        return true;
      }
    }
    return false;
  }

private:
  std::array<Entry, Capacity> entries_{};
  std::size_t                 count_ = 0;
};

} // namespace nova::dma_device
