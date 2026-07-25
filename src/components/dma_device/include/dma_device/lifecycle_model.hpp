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

// --- Lifecycle walk classification (pure, host-testable) ---------------------
//
// Every quiesce/resume walk visits the VM's entries and reacts to each
// state; these verdicts ARE the transition table, extracted so the
// component's locked walks reduce to classify → act and the matrix is
// testable without hardware.

enum class ScanAction : std::uint8_t {
  kSkip,    // not this walk's concern (unavailable, or already at the target)
  kFail,    // the VM must fail closed
  kPending, // not ready yet — retry through the poll path
  kCollect, // act on this device in this walk
};

// begin_quiesce: only kActive entries start a new quiesce. kPending
// here means "already mid-quiesce — keep walking and converge through
// complete_quiesce"; a mid-resume entry is a protocol break.
[[nodiscard]] constexpr auto classify_begin_quiesce(State s) noexcept -> ScanAction {
  switch (s) {
  case State::kUnavailable:
  case State::kQuiesced:
    return ScanAction::kSkip;
  case State::kQuiescing:
  case State::kDetaching:
    return ScanAction::kPending;
  case State::kActive:
    return ScanAction::kCollect;
  case State::kFailed:
  case State::kResuming:
    return ScanAction::kFail;
  }
  return ScanAction::kFail;
}

// complete_quiesce / poll_quiesce: only a fully bus-master-blocked
// kQuiescing entry advances; kFailed fails closed; anything else means
// the walk ran ahead of the state machine.
[[nodiscard]] constexpr auto classify_quiescing(State s, bool bus_master_blocked) noexcept -> ScanAction {
  switch (s) {
  case State::kUnavailable:
  case State::kQuiesced:
    return ScanAction::kSkip;
  case State::kQuiescing:
    return bus_master_blocked ? ScanAction::kCollect : ScanAction::kPending;
  case State::kDetaching:
  case State::kResuming:
  case State::kActive:
    return ScanAction::kPending;
  case State::kFailed:
    return ScanAction::kFail;
  }
  return ScanAction::kFail;
}

// resume_vm: only a fully quiesced VM may adopt a new generation.
[[nodiscard]] constexpr auto classify_resume(State s) noexcept -> ScanAction {
  switch (s) {
  case State::kUnavailable:
    return ScanAction::kSkip;
  case State::kQuiesced:
    return ScanAction::kCollect;
  case State::kQuiescing:
  case State::kDetaching:
  case State::kResuming:
  case State::kActive:
  case State::kFailed:
    return ScanAction::kFail;
  }
  return ScanAction::kFail;
}

template <std::size_t Capacity>
class Registry {
public:
  constexpr void reset() noexcept {
    entries_ = {};
    count_   = 0;
  }

  [[nodiscard]] constexpr auto load(std::span<const dma::Assignment> assignments) noexcept -> bool {
    reset();
    for (const dma::Assignment& assignment : assignments) {
      if (!add(assignment.device_id, assignment.vm)) {
        reset();
        return false;
      }
    }
    return true;
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

  constexpr void fail_owner(std::size_t vm) noexcept {
    for (Entry& entry : entries()) {
      if (entry.owner_vm == vm && entry.state != State::kUnavailable) {
        entry.state              = State::kFailed;
        entry.generation         = 0;
        entry.bus_master_blocked = true;
      }
    }
  }

private:
  std::array<Entry, Capacity> entries_{};
  std::size_t                 count_ = 0;
};

} // namespace nova::dma_device
