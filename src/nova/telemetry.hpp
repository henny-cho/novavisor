#pragma once

// nova/telemetry.hpp
//
// The publisher of the S layer's region, over the layout in
// nova/abi/telemetry.h — and the reader beside it, so the seqlock is
// proven under real host-thread concurrency before anything decodes
// what it hands out. The Python reader mirrors this one.
//
// Pure and host-testable. Nothing here knows what a slot means; this
// file guarantees two things and no more: bytes a reader accepts were
// all one reading, and a sequence moves exactly when its bytes do.
//
// Explicitly no memcpy: EL2 must stay FP-free, and the libc mem*
// routines reach for SIMD. The copy is written out.

#include "nova/abi/telemetry.h"
#include "nova/sync.hpp"

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>

namespace nova::telemetry {

// The descriptor and header layouts, mirrored from the ABI header. The
// static_asserts are what make these mirrors rather than second
// opinions: change a #define and the build stops here.
struct Descriptor {
  std::uint64_t source = 0;
  std::uint64_t seq    = 0;
  std::uint64_t stamp  = 0;
  std::uint32_t at     = 0;
  std::uint32_t bytes  = 0;
};

static_assert(sizeof(Descriptor) == NOVA_TLM_DESC_SIZE);
static_assert(offsetof(Descriptor, source) == NOVA_TLM_DESC_SOURCE_OFF);
static_assert(offsetof(Descriptor, seq) == NOVA_TLM_DESC_SEQ_OFF);
static_assert(offsetof(Descriptor, stamp) == NOVA_TLM_DESC_STAMP_OFF);
static_assert(offsetof(Descriptor, at) == NOVA_TLM_DESC_AT_OFF);
static_assert(offsetof(Descriptor, bytes) == NOVA_TLM_DESC_BYTES_OFF);

struct Header {
  std::uint64_t magic     = 0;
  std::uint32_t version   = 0;
  std::uint32_t slots     = 0;
  std::uint32_t max_slots = 0;
  std::uint32_t desc_size = 0;
  std::uint32_t period_us = 0;
  std::uint32_t budget    = 0;
  std::uint32_t bytes     = 0;
  std::uint32_t freq      = 0;
};

static_assert(offsetof(Header, magic) == NOVA_TLM_MAGIC_OFF);
static_assert(offsetof(Header, version) == NOVA_TLM_VERSION_OFF);
static_assert(offsetof(Header, slots) == NOVA_TLM_SLOTS_OFF);
static_assert(offsetof(Header, max_slots) == NOVA_TLM_MAXSLOTS_OFF);
static_assert(offsetof(Header, desc_size) == NOVA_TLM_DESCSIZE_OFF);
static_assert(offsetof(Header, period_us) == NOVA_TLM_PERIOD_OFF);
static_assert(offsetof(Header, budget) == NOVA_TLM_BUDGET_OFF);
static_assert(offsetof(Header, bytes) == NOVA_TLM_BYTES_OFF);
static_assert(offsetof(Header, freq) == NOVA_TLM_FREQ_OFF);
static_assert(sizeof(Header) <= NOVA_TLM_HEADER_SIZE);

// The table cannot reach the payloads, and the payloads start where a
// word copy can begin. Checked rather than asserted in prose, so the
// constants carry their own justification.
static_assert(NOVA_TLM_HEADER_SIZE + (NOVA_TLM_MAX_SLOTS * NOVA_TLM_DESC_SIZE) <= NOVA_TLM_PAYLOAD_OFF,
              "the descriptor table overruns the payload area");
static_assert(NOVA_TLM_PAYLOAD_OFF % NOVA_TLM_ALIGN == 0);
static_assert(NOVA_TLM_REGION_SIZE == NOVA_TLM_PAYLOAD_OFF + NOVA_TLM_PAYLOAD_BYTES);

// One publishable span: where a component's global is, and how much of
// it travels.
//
// Components hand these over rather than the publisher reaching in.
// Most of what is worth publishing is TU-private
// (nova::vgic::(anonymous)::g_cpu), so the owning translation unit is
// the only place its address can be taken — which is also the only
// place that knows the span is still the right one.
struct Span {
  const void* at    = nullptr;
  std::size_t bytes = 0;
};

// Copy `bytes` from `source` over `destination`, reporting whether any
// byte differed.
//
// One pass answers both questions. The destination already holds the
// previous reading, so the comparison reads a word the copy has to
// write anyway — which is what lets the sequence mean "the value moved"
// without a second walk to find out.
//
// Words while both sides are aligned and a whole one remains, then
// bytes. The spans are whole C++ objects and not all of them are a
// multiple of a word, so the tail is what the smallest of them leave
// behind rather than an edge case.
[[nodiscard]] inline auto copy_changed(void* destination, const void* source, std::size_t bytes) noexcept -> bool {
  auto*                 out   = static_cast<unsigned char*>(destination);
  const auto*           in    = static_cast<const unsigned char*>(source);
  bool                  moved = false;
  std::size_t           index = 0;
  constexpr std::size_t kWord = sizeof(std::uint64_t);
  if ((reinterpret_cast<std::uintptr_t>(out) % kWord) == 0 && (reinterpret_cast<std::uintptr_t>(in) % kWord) == 0) {
    for (; index + kWord <= bytes; index += kWord) {
      auto*               word  = reinterpret_cast<std::uint64_t*>(out + index);
      const std::uint64_t value = *reinterpret_cast<const std::uint64_t*>(in + index);
      moved |= (*word != value);
      *word = value;
    }
  }
  for (; index < bytes; ++index) {
    moved |= (out[index] != in[index]);
    out[index] = in[index];
  }
  return moved;
}

// One publisher over one region. Default-constructed it is inert, which
// is what EL2 runs with before the region is placed — a turn copies
// nothing rather than faulting.
class Publisher {
public:
  Publisher() = default;

  // Bind to a region and lay out its header: geometry, an empty slot
  // table, and nothing else. Deliberately not the magic — that flag
  // means "everything beside me is now true", and the slots are
  // declared after this.
  //
  // A method rather than a constructor because this object holds a lock
  // and so cannot be assigned; binding in place is what the one caller
  // wanted anyway.
  void bind(void* base, std::uint32_t period_us, std::uint32_t budget, std::uint32_t freq) noexcept {
    base_ = static_cast<unsigned char*>(base);
    // The region is laid out again, so what this object remembers about
    // it goes too. Only one caller binds once today, and that is why
    // this has to be here rather than because of it.
    slots_ = used_ = copied_ = cursor_ = 0;
    // Header and table back to zero. The payloads are not cleared: a
    // slot's first turn writes it whole, and until then no descriptor
    // points at it.
    for (std::size_t offset = 0; offset < NOVA_TLM_PAYLOAD_OFF; ++offset) {
      base_[offset] = 0;
    }
    Header& header   = *reinterpret_cast<Header*>(base_);
    header.version   = NOVA_TLM_VERSION;
    header.max_slots = NOVA_TLM_MAX_SLOTS;
    header.desc_size = NOVA_TLM_DESC_SIZE;
    header.period_us = period_us;
    header.budget    = budget;
    header.freq      = freq;
  }

  [[nodiscard]] auto placed() const noexcept -> bool { return base_ != nullptr; }
  [[nodiscard]] auto slots() const noexcept -> std::size_t { return slots_; }
  [[nodiscard]] auto bytes() const noexcept -> std::size_t { return copied_; }

  // Register one span, identified by the address of the global it
  // copies. False when the region cannot hold it: that slot is simply
  // not published, which a reader sees as a symbol it cannot find,
  // rather than a payload landing on top of another one's.
  [[nodiscard]] auto declare(const void* source, std::size_t bytes) noexcept -> bool {
    if (!placed() || slots_ >= NOVA_TLM_MAX_SLOTS || bytes == 0) {
      return false;
    }
    // Padded to the copy's granularity so the next payload starts where
    // a word loop may begin; the descriptor still names the object's
    // own size, which is what travels.
    const std::size_t stride = (bytes + NOVA_TLM_ALIGN - 1) & ~(static_cast<std::size_t>(NOVA_TLM_ALIGN) - 1);
    if (used_ + stride > NOVA_TLM_PAYLOAD_BYTES) {
      return false;
    }
    Descriptor& descriptor = table()[slots_];
    descriptor.source      = reinterpret_cast<std::uintptr_t>(source);
    descriptor.at          = static_cast<std::uint32_t>(NOVA_TLM_PAYLOAD_OFF + used_);
    descriptor.bytes       = static_cast<std::uint32_t>(bytes);
    used_ += stride;
    copied_ += bytes;
    ++slots_;
    return true;
  }

  // Make the region findable, last: a reader that finds the magic finds
  // every slot this build declares, already sized and placed.
  void open() noexcept {
    if (!placed()) {
      return;
    }
    Header& header = *reinterpret_cast<Header*>(base_);
    header.slots   = static_cast<std::uint32_t>(slots_);
    header.bytes   = static_cast<std::uint32_t>(copied_);
    std::atomic_ref{header.magic}.store(NOVA_TLM_MAGIC, std::memory_order_release);
  }

  // Copy from wherever the last turn stopped until the budget is spent,
  // and leave the cursor there.
  //
  // A budget rather than "every slot every turn" so the cost of a turn
  // is a number this layer states instead of a consequence of how many
  // slots happen to be registered. Today's spans fit in one turn; a
  // later set that does not degrades into more turns rather than into a
  // longer interrupt. A slot is never split, so the last one of a turn
  // may cross the line — the budget bounds a turn, it does not divide
  // a reading.
  auto publish(std::uint64_t stamp) noexcept -> std::size_t {
    if (!placed() || slots_ == 0) {
      return 0;
    }
    const sync::Guard held{turn_};
    const std::size_t budget = reinterpret_cast<const Header*>(base_)->budget;
    std::size_t       spent  = 0;
    for (std::size_t visited = 0; visited < slots_ && spent < budget; ++visited) {
      spent += publish_slot(cursor_, stamp);
      cursor_ = (cursor_ + 1) % slots_;
    }
    return spent;
  }

  // Every slot, once, whatever the budget says.
  //
  // For the moment the machine stops. A publisher's readings are only
  // as fresh as its next turn, and there is no next turn after a halt —
  // so without this, the one state a reader most wants is the one state
  // never published. The budget exists to bound a recurring interrupt;
  // there is nothing left to bound here.
  auto publish_all(std::uint64_t stamp) noexcept -> std::size_t {
    if (!placed()) {
      return 0;
    }
    const sync::Guard held{turn_};
    std::size_t       spent = 0;
    for (std::size_t index = 0; index < slots_; ++index) {
      spent += publish_slot(index, stamp);
    }
    return spent;
  }

private:
  [[nodiscard]] auto table() noexcept -> Descriptor* {
    return reinterpret_cast<Descriptor*>(base_ + NOVA_TLM_HEADER_SIZE);
  }

  // One slot, under the sequence that guards it.
  auto publish_slot(std::size_t index, std::uint64_t stamp) noexcept -> std::size_t {
    Descriptor&         descriptor = table()[index];
    const std::uint64_t start      = std::atomic_ref{descriptor.seq}.load(std::memory_order_relaxed);
    // Odd first, so a reader arriving mid-copy retries rather than
    // assembling one value out of two readings.
    std::atomic_ref{descriptor.seq}.store(start + 1, std::memory_order_relaxed);
    std::atomic_thread_fence(std::memory_order_release);
    const bool moved =
        copy_changed(base_ + descriptor.at, reinterpret_cast<const void*>(descriptor.source), descriptor.bytes);
    if (moved) {
      descriptor.stamp = stamp;
    }
    std::atomic_thread_fence(std::memory_order_release);
    // Back where it was when nothing moved. The window did open and the
    // bytes were rewritten, but with the values already there, so a
    // reader that retried across it reads what it would have read
    // before — and the sequence goes on meaning "the value moved"
    // rather than "a period elapsed".
    std::atomic_ref{descriptor.seq}.store(moved ? start + 2 : start, std::memory_order_relaxed);
    return descriptor.bytes;
  }

  // A sequence is single-writer by construction, and there are two
  // callers on different cores: the periodic turn on the primary, and
  // the final turn on whichever core finds the machine empty. Two of
  // them interleaved would leave a sequence even with bytes from both
  // — the one thing this layer promises cannot happen. The wait is
  // bounded because a turn calls nothing that can block and no core
  // ever holds this while entering the scheduler.
  sync::SpinLock turn_{};

  unsigned char* base_   = nullptr;
  std::size_t    slots_  = 0;
  std::size_t    used_   = 0; // payload watermark, padded
  std::size_t    copied_ = 0; // what a full sweep moves
  std::size_t    cursor_ = 0;
};

// What one attempt at a slot came back with. `stable` false means the
// writer was inside the window or crossed it: the bytes are not a
// reading and the caller tries again.
struct Reading {
  bool          stable = false;
  std::uint64_t seq    = 0;
  std::uint64_t stamp  = 0;
  std::size_t   bytes  = 0;
};

// Reader side, mirrored by the Python one. Takes a mutable base because
// reading the sequence goes through atomic_ref, which needs a non-const
// lvalue; nothing here writes to the region.
[[nodiscard]] inline auto read_slot(void* base, std::size_t index, void* out, std::size_t capacity) noexcept
    -> Reading {
  Reading     reading{};
  auto*       bytes      = static_cast<unsigned char*>(base);
  Descriptor& descriptor = reinterpret_cast<Descriptor*>(bytes + NOVA_TLM_HEADER_SIZE)[index];

  const std::uint64_t before = std::atomic_ref{descriptor.seq}.load(std::memory_order_acquire);
  if ((before & 1U) != 0U || descriptor.bytes > capacity) {
    return reading;
  }
  auto* out_bytes = static_cast<unsigned char*>(out);
  for (std::uint32_t index_in_slot = 0; index_in_slot < descriptor.bytes; ++index_in_slot) {
    out_bytes[index_in_slot] = bytes[descriptor.at + index_in_slot];
  }
  const std::uint64_t stamp = descriptor.stamp;
  std::atomic_thread_fence(std::memory_order_acquire);

  reading.stable = std::atomic_ref{descriptor.seq}.load(std::memory_order_relaxed) == before;
  reading.seq    = before;
  reading.stamp  = stamp;
  reading.bytes  = descriptor.bytes;
  return reading;
}

// The region and the publisher EL2 uses. Storage lives beside the model,
// like the trace rings and the command page, so the symbol a bridge
// resolves is a property of this layout rather than of whichever
// component places it.
//
// Aligned to the payload boundary so the whole region is a whole number
// of pages: a host maps exactly this much read-only and nothing else.
struct alignas(NOVA_TLM_PAYLOAD_OFF) Region {
  std::array<unsigned char, NOVA_TLM_REGION_SIZE> byte{};
};

inline Region    g_region{};
inline Publisher g_publisher{};

} // namespace nova::telemetry
