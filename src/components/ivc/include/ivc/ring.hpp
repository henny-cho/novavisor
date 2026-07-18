#pragma once

// components/ivc/include/ivc/ring.hpp
//
// SPSC ring over the nova/abi/ivc_ring.h layout — the host-testable
// model of the protocol the guests speak in the IVC shared page.
// Single producer, single consumer, no read-modify-write: the producer
// owns widx, the consumer owns ridx, and each side only load-acquires
// the other's index. Payload visibility rides the release stores.
//
// The hypervisor never touches ring payloads at runtime (the page is
// guest-owned); this model exists so the ordering protocol is proven
// under real host-thread concurrency before a guest relies on it.

#include "nova/abi/ivc_ring.h"

#include <atomic>
#include <cstdint>

namespace nova::ivc {

class Ring {
public:
  // `base` is the ring's base address inside the shared page (caller
  // adds NOVA_IVC_RING0_OFF / NOVA_IVC_RING1_OFF), 64-byte aligned.
  explicit Ring(void* base) noexcept
      : widx_(reinterpret_cast<std::uint32_t*>(static_cast<char*>(base) + NOVA_IVC_RING_WIDX_OFF)),
        ridx_(reinterpret_cast<std::uint32_t*>(static_cast<char*>(base) + NOVA_IVC_RING_RIDX_OFF)),
        slots_(reinterpret_cast<std::uint64_t*>(static_cast<char*>(base) + NOVA_IVC_RING_SLOTS_OFF)) {}

  // Producer side. False when full.
  [[nodiscard]] auto push(std::uint64_t value) noexcept -> bool {
    const std::uint32_t w = std::atomic_ref{*widx_}.load(std::memory_order_relaxed); // producer-owned
    const std::uint32_t r = std::atomic_ref{*ridx_}.load(std::memory_order_acquire);
    if (w - r == NOVA_IVC_RING_SLOTS) {
      return false;
    }
    slots_[w & (NOVA_IVC_RING_SLOTS - 1)] = value;
    std::atomic_ref{*widx_}.store(w + 1, std::memory_order_release); // publishes the payload
    return true;
  }

  // Consumer side. False when empty.
  [[nodiscard]] auto pop(std::uint64_t& value) noexcept -> bool {
    const std::uint32_t r = std::atomic_ref{*ridx_}.load(std::memory_order_relaxed); // consumer-owned
    const std::uint32_t w = std::atomic_ref{*widx_}.load(std::memory_order_acquire);
    if (w == r) {
      return false;
    }
    value = slots_[r & (NOVA_IVC_RING_SLOTS - 1)];
    std::atomic_ref{*ridx_}.store(r + 1, std::memory_order_release); // frees the slot
    return true;
  }

private:
  std::uint32_t* widx_;
  std::uint32_t* ridx_;
  std::uint64_t* slots_;
};

} // namespace nova::ivc
