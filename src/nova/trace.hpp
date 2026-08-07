#pragma once

// nova/trace.hpp
//
// The T layer's writer: per-CPU overwriting event rings over the
// nova/abi/trace_ring.h layout.
//
// Pure and host-testable. The base address is injected rather than read
// from a board header, so this file has no platform dependency and the
// ordering protocol can be proven under real host-thread concurrency
// before two cores rely on it — the same split ivc/ring.hpp uses.
//
// The write path is a handful of stores and one release. It never
// fails, never blocks, and never consults the reader: a core owns its
// ring outright, so there is no cross-core atomic here, and a host that
// stops reading costs nothing but overwritten history. That property is
// the point — observation must not be able to stall what it observes.
//
// Explicitly no memcpy: EL2 must stay FP-free, and the libc mem*
// routines reach for SIMD. Fields are stored one at a time.

#include "nova/abi/trace_ring.h"

#include <atomic>
#include <cstddef>
#include <cstdint>

namespace nova::trace {

// The record and region layouts, mirrored from the ABI header. The
// static_asserts are what make this a mirror rather than a second
// opinion: change a #define and the build stops here.
struct Record {
  std::uint64_t ts    = 0;
  std::uint16_t type  = 0;
  std::uint8_t  cpu   = 0;
  std::uint8_t  flags = 0;
  std::uint32_t a     = 0;
  std::uint64_t b     = 0;
  std::uint64_t c     = 0;
};

static_assert(sizeof(Record) == NOVA_TRACE_REC_SIZE);
static_assert(offsetof(Record, ts) == NOVA_TRACE_TS_OFF);
static_assert(offsetof(Record, type) == NOVA_TRACE_TYPE_OFF);
static_assert(offsetof(Record, cpu) == NOVA_TRACE_CPU_OFF);
static_assert(offsetof(Record, flags) == NOVA_TRACE_FLAG_OFF);
static_assert(offsetof(Record, a) == NOVA_TRACE_A_OFF);
static_assert(offsetof(Record, b) == NOVA_TRACE_B_OFF);
static_assert(offsetof(Record, c) == NOVA_TRACE_C_OFF);

struct Header {
  std::uint64_t magic       = 0;
  std::uint32_t version     = 0;
  std::uint32_t record_size = 0;
  std::uint32_t stride      = 0;
  std::uint32_t rings       = 0;
  std::uint32_t capacity    = 0;
  std::uint32_t freq_hz     = 0;
};

static_assert(offsetof(Header, magic) == NOVA_TRACE_MAGIC_OFF);
static_assert(offsetof(Header, version) == NOVA_TRACE_VERSION_OFF);
static_assert(offsetof(Header, record_size) == NOVA_TRACE_RECSIZE_OFF);
static_assert(offsetof(Header, stride) == NOVA_TRACE_STRIDE_OFF);
static_assert(offsetof(Header, rings) == NOVA_TRACE_RINGS_OFF);
static_assert(offsetof(Header, capacity) == NOVA_TRACE_CAP_OFF);
static_assert(offsetof(Header, freq_hz) == NOVA_TRACE_FREQ_OFF);
static_assert(sizeof(Header) <= NOVA_TRACE_HEADER_SIZE);

inline constexpr std::uint64_t kCapacityMask = NOVA_TRACE_CAPACITY - 1;
static_assert((NOVA_TRACE_CAPACITY & kCapacityMask) == 0, "capacity must be a power of two");

// Bytes one ring occupies, header included.
inline constexpr std::size_t kRingStride = NOVA_TRACE_RECORDS_OFF + NOVA_TRACE_CAPACITY * NOVA_TRACE_REC_SIZE;

[[nodiscard]] constexpr auto region_size(std::size_t rings) noexcept -> std::size_t {
  return NOVA_TRACE_HEADER_SIZE + rings * kRingStride;
}

// One core's ring. Default-constructed it is inert, which is what a
// core runs with before the region is placed and what a host test gets
// for free — an unplaced ring drops events rather than faulting.
class Ring {
public:
  Ring() = default;

  explicit Ring(void* base) noexcept
      : head_(reinterpret_cast<std::uint64_t*>(static_cast<char*>(base) + NOVA_TRACE_HEAD_OFF)),
        records_(reinterpret_cast<Record*>(static_cast<char*>(base) + NOVA_TRACE_RECORDS_OFF)) {}

  [[nodiscard]] auto placed() const noexcept -> bool { return head_ != nullptr; }

  // Records produced since the region was formatted. Monotonic: it is
  // never reduced, so the reader can tell a lap from a rewind.
  [[nodiscard]] auto head() const noexcept -> std::uint64_t {
    return placed() ? std::atomic_ref{*head_}.load(std::memory_order_acquire) : 0;
  }

  // The write path. `head` is core-private, so the load is relaxed; the
  // store is a release, and it is what publishes the body written just
  // above it. A reader never looks at or past `head`, so it cannot see
  // a half-written record unless the writer laps it during the copy —
  // which the reader detects by re-reading `head` afterwards.
  void emit(std::uint64_t ts, std::uint16_t type, std::uint8_t cpu, std::uint32_t a, std::uint64_t b,
            std::uint64_t c) noexcept {
    if (!placed()) {
      return;
    }
    const std::uint64_t index = std::atomic_ref{*head_}.load(std::memory_order_relaxed);
    Record&             slot  = records_[index & kCapacityMask];
    slot.ts                   = ts;
    slot.type                 = type;
    slot.cpu                  = cpu;
    slot.flags                = 0;
    slot.a                    = a;
    slot.b                    = b;
    slot.c                    = c;
    std::atomic_ref{*head_}.store(index + 1, std::memory_order_release);
  }

private:
  std::uint64_t* head_    = nullptr;
  Record*        records_ = nullptr;
};

// Where ring `index` begins inside a region at `base`.
[[nodiscard]] inline auto ring_at(void* base, std::size_t index) noexcept -> void* {
  return static_cast<char*>(base) + NOVA_TRACE_HEADER_SIZE + index * kRingStride;
}

// Publish the region's geometry and zero every head.
//
// The magic goes last. A reader that finds it can then trust everything
// beside it, so a region caught mid-format reads as absent rather than
// as a ring with a plausible but wrong stride.
inline void format(void* base, std::uint32_t rings, std::uint32_t freq_hz) noexcept {
  auto* header        = reinterpret_cast<Header*>(base);
  header->version     = NOVA_TRACE_VERSION;
  header->record_size = NOVA_TRACE_REC_SIZE;
  header->stride      = static_cast<std::uint32_t>(kRingStride);
  header->rings       = rings;
  header->capacity    = NOVA_TRACE_CAPACITY;
  header->freq_hz     = freq_hz;
  for (std::uint32_t index = 0; index < rings; ++index) {
    auto* head = reinterpret_cast<std::uint64_t*>(static_cast<char*>(ring_at(base, index)) + NOVA_TRACE_HEAD_OFF);
    std::atomic_ref{*head}.store(0, std::memory_order_relaxed);
  }
  std::atomic_ref{header->magic}.store(NOVA_TRACE_MAGIC, std::memory_order_release);
}

} // namespace nova::trace
