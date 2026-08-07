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

#include <array>
#include <atomic>
#include <bit>
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
  std::uint32_t early       = 0;
};

static_assert(offsetof(Header, magic) == NOVA_TRACE_MAGIC_OFF);
static_assert(offsetof(Header, version) == NOVA_TRACE_VERSION_OFF);
static_assert(offsetof(Header, record_size) == NOVA_TRACE_RECSIZE_OFF);
static_assert(offsetof(Header, stride) == NOVA_TRACE_STRIDE_OFF);
static_assert(offsetof(Header, rings) == NOVA_TRACE_RINGS_OFF);
static_assert(offsetof(Header, capacity) == NOVA_TRACE_CAP_OFF);
static_assert(offsetof(Header, freq_hz) == NOVA_TRACE_FREQ_OFF);
static_assert(offsetof(Header, early) == NOVA_TRACE_EARLY_OFF);
static_assert(sizeof(Header) <= NOVA_TRACE_HEADER_SIZE);

// Bytes one ring occupies, header included.
[[nodiscard]] constexpr auto ring_stride(std::size_t capacity) noexcept -> std::size_t {
  return NOVA_TRACE_RECORDS_OFF + capacity * NOVA_TRACE_REC_SIZE;
}

[[nodiscard]] constexpr auto region_size(std::size_t rings, std::size_t capacity) noexcept -> std::size_t {
  return NOVA_TRACE_HEADER_SIZE + rings * ring_stride(capacity);
}

// How deep a ring gets, given the region it shares and how many share
// it. This is the inverse of region_size(), and it is the direction the
// real system runs in: a board reserves bytes, a build fixes the core
// count, and the capacity is whatever those two divide into. Nobody
// declares it, so nobody can declare it wrong.
//
// Floored to a power of two so indexing stays a mask. The floor is why
// the region wants a little slack above the arithmetic minimum —
// landing one record short of a boundary halves every ring.
[[nodiscard]] constexpr auto records_per_ring(std::size_t size, std::size_t rings) noexcept -> std::size_t {
  if (rings == 0 || size < NOVA_TRACE_HEADER_SIZE) {
    return 0;
  }
  const std::size_t per_ring = (size - NOVA_TRACE_HEADER_SIZE) / rings;
  if (per_ring <= NOVA_TRACE_RECORDS_OFF) {
    return 0;
  }
  return std::bit_floor((per_ring - NOVA_TRACE_RECORDS_OFF) / NOVA_TRACE_REC_SIZE);
}

// Events emitted before any ring was placed. There is nowhere to put
// them, so they are lost by construction — but a loss nobody counts is
// indistinguishable from nothing having happened, and this one falls
// in early boot, where that difference matters most. place() folds the
// total into the region header.
inline std::atomic<std::uint32_t> g_early{};

// One core's ring. Default-constructed it is inert, which is what a
// core runs with before the region is placed and what a host test gets
// for free — an unplaced ring counts events rather than faulting.
class Ring {
public:
  Ring() = default;

  // The mask travels with the ring rather than sitting in a constant
  // beside it: the depth is a property of the region this ring was cut
  // from, and a writer that read it from anywhere else would be the
  // second opinion the derivation exists to remove.
  Ring(void* base, std::size_t capacity) noexcept
      : head_(reinterpret_cast<std::uint64_t*>(static_cast<char*>(base) + NOVA_TRACE_HEAD_OFF)),
        records_(reinterpret_cast<Record*>(static_cast<char*>(base) + NOVA_TRACE_RECORDS_OFF)), mask_(capacity - 1) {}

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
      g_early.fetch_add(1, std::memory_order_relaxed);
      return;
    }
    const std::uint64_t index = std::atomic_ref{*head_}.load(std::memory_order_relaxed);
    Record&             slot  = records_[index & mask_];
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
  std::uint64_t  mask_    = 0;
};

// The rings themselves, one per core, filled in by whoever places the
// region. An inline variable rather than a component global: the hot
// paths that emit must not acquire a link dependency on the component
// that happens to do the placing, and a profile without it simply
// leaves these unplaced, where emit() drops.
inline std::array<Ring, NOVA_TRACE_MAX_RINGS> g_ring{};

// Where ring `index` begins inside a region at `base`.
[[nodiscard]] inline auto ring_at(void* base, std::size_t index, std::size_t capacity) noexcept -> void* {
  return static_cast<char*>(base) + NOVA_TRACE_HEADER_SIZE + index * ring_stride(capacity);
}

// Lay out the region: geometry fields, and every head back to zero.
//
// Deliberately not the magic. That flag means "everything beside me is
// now true", and one header field — the count of events that had no
// ring to land in — cannot be final until the rings are bound. Writing
// it after the magic would leave a reader free to sample it early and
// cache a zero, so the magic is publish()'s to release.
inline void format(void* base, std::uint32_t rings, std::uint32_t capacity, std::uint32_t freq_hz) noexcept {
  auto* header        = reinterpret_cast<Header*>(base);
  header->version     = NOVA_TRACE_VERSION;
  header->record_size = NOVA_TRACE_REC_SIZE;
  header->stride      = static_cast<std::uint32_t>(ring_stride(capacity));
  header->rings       = rings;
  header->capacity    = capacity;
  header->freq_hz     = freq_hz;
  header->early       = 0;
  // Heads only. Clearing the records would put a multi-megabyte memset
  // on the boot path to hide bytes the reader's own window arithmetic
  // already excludes — the window starts at head - capacity, so a
  // previous boot's tail is never inside it.
  for (std::uint32_t index = 0; index < rings; ++index) {
    auto* head =
        reinterpret_cast<std::uint64_t*>(static_cast<char*>(ring_at(base, index, capacity)) + NOVA_TRACE_HEAD_OFF);
    std::atomic_ref{*head}.store(0, std::memory_order_relaxed);
  }
}

// Make a formatted region findable, and account for what preceded it.
//
// The magic goes last, so a region caught mid-format reads as absent
// rather than as a ring with a plausible but wrong stride.
inline void publish(void* base, std::uint32_t early) noexcept {
  auto* header  = reinterpret_cast<Header*>(base);
  header->early = early;
  std::atomic_ref{header->magic}.store(NOVA_TRACE_MAGIC, std::memory_order_release);
}

// Divide `size` bytes at `base` into `rings` rings, bind every core's
// ring to one, then make the region findable.
//
// The depth is computed here and nowhere else. The caller supplies the
// two facts it actually owns — how much the board reserved and how many
// cores will write — and everything else is arithmetic, so there is no
// value for a board header and this file to disagree about.
//
// The order is the point. Heads are zeroed before any ring is bound, so
// no event lands at a stale index that the zeroing then discards; the
// rings are bound before the magic, so an event in that window is
// recorded rather than counted as early; and `early` is read once the
// last ring is placed, after which no core can add to it — the board
// seam in the trace component asserts that every core has a ring.
//
// Runs once, on the primary, before any secondary exists — the same
// ordering premise the boot CTR_EL0 snapshot relies on, so a secondary
// that starts later reads rings that are already placed.
inline void place(void* base, std::size_t size, std::size_t rings, std::uint32_t freq_hz) noexcept {
  const std::size_t count    = rings < NOVA_TRACE_MAX_RINGS ? rings : NOVA_TRACE_MAX_RINGS;
  const std::size_t capacity = records_per_ring(size, count);
  if (capacity == 0) {
    // Too little room for even one record. Left unformatted on purpose:
    // an unplaced ring already counts what it drops, and publishing a
    // magic over a geometry nothing can be read from would trade a
    // truthful "nothing here" for a region a reader must reject. The
    // board seam's floor is what keeps a real build off this path.
    return;
  }
  format(base, static_cast<std::uint32_t>(count), static_cast<std::uint32_t>(capacity), freq_hz);
  for (std::size_t index = 0; index < count; ++index) {
    g_ring[index] = Ring{ring_at(base, index, capacity), capacity};
  }
  publish(base, g_early.load(std::memory_order_relaxed));
}

} // namespace nova::trace
