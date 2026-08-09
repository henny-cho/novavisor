#pragma once

// nova/command.hpp
//
// The consumer of the host's command ring, over the layout in
// nova/abi/command_ring.h — and the producer beside it, so the protocol
// can be proven under real host-thread concurrency before EL2 acts on
// anything it delivers. The Python producer mirrors this one.
//
// Pure and host-testable. Nothing here knows what an opcode means; this
// file only guarantees that what EL2 reads is what the host wrote,
// once, in order.
//
// The consumer treats the producer as untrusted. The slot index is
// taken modulo a compile-time power of two, so it cannot leave the
// page; a drain stops at the index it read on entry and at the ring's
// depth, so it cannot be lengthened; and a producer that has broken the
// protocol outright is resynchronised rather than obeyed.
//
// Explicitly no memcpy: EL2 must stay FP-free, and the libc mem*
// routines reach for SIMD. Fields are stored one at a time.

#include "nova/abi/command_ring.h"

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>

namespace nova::command {

// The record and page layouts, mirrored from the ABI header. The
// static_asserts are what make this a mirror rather than a second
// opinion: change a #define and the build stops here.
struct Record {
  std::uint64_t op = 0;
  std::uint64_t a  = 0;
  std::uint64_t b  = 0;
};

static_assert(sizeof(Record) == NOVA_CMD_REC_SIZE);
static_assert(offsetof(Record, op) == NOVA_CMD_REC_OP_OFF);
static_assert(offsetof(Record, a) == NOVA_CMD_REC_A_OFF);
static_assert(offsetof(Record, b) == NOVA_CMD_REC_B_OFF);

// The bands EL2 checks an argument against, published so the host can
// ask the machine what it accepts. Filled by whoever places the page,
// which is the component that owns the checks.
struct Limits {
  std::uint32_t slice_min_us = 0;
  std::uint32_t slice_def_us = 0;
  std::uint32_t slice_max_us = 0;
  std::uint32_t spi_lo       = 0;
  std::uint32_t spi_hi       = 0;
};

struct Header {
  std::uint64_t magic        = 0;
  std::uint32_t version      = 0;
  std::uint32_t record_size  = 0;
  std::uint32_t slots        = 0;
  std::uint32_t period_us    = 0;
  std::uint32_t slice_min_us = 0;
  std::uint32_t slice_def_us = 0;
  std::uint32_t slice_max_us = 0;
  std::uint32_t spi_lo       = 0;
  std::uint32_t spi_hi       = 0;
};

static_assert(offsetof(Header, magic) == NOVA_CMD_MAGIC_OFF);
static_assert(offsetof(Header, version) == NOVA_CMD_VERSION_OFF);
static_assert(offsetof(Header, record_size) == NOVA_CMD_RECSIZE_OFF);
static_assert(offsetof(Header, slots) == NOVA_CMD_SLOTS_OFF);
static_assert(offsetof(Header, period_us) == NOVA_CMD_PERIOD_OFF);
static_assert(offsetof(Header, slice_min_us) == NOVA_CMD_SLICE_MIN_OFF);
static_assert(offsetof(Header, slice_def_us) == NOVA_CMD_SLICE_DEF_OFF);
static_assert(offsetof(Header, slice_max_us) == NOVA_CMD_SLICE_MAX_OFF);
static_assert(offsetof(Header, spi_lo) == NOVA_CMD_SPI_LO_OFF);
static_assert(offsetof(Header, spi_hi) == NOVA_CMD_SPI_HI_OFF);
static_assert(sizeof(Header) <= NOVA_CMD_WIDX_OFF);

// The depth is a mask, and the deepest the page allows: doubling it
// would not fit. Checked rather than asserted in prose, so the constant
// carries its own justification.
static_assert((NOVA_CMD_SLOTS & (NOVA_CMD_SLOTS - 1)) == 0);
static_assert(NOVA_CMD_RECORDS_OFF + NOVA_CMD_SLOTS * NOVA_CMD_REC_SIZE <= NOVA_CMD_PAGE);
static_assert(NOVA_CMD_RECORDS_OFF + 2 * NOVA_CMD_SLOTS * NOVA_CMD_REC_SIZE > NOVA_CMD_PAGE,
              "the page holds more commands than this ring offers");

// The answering record's two halves, tied so they cannot overlap: the
// mask is exactly what fits below the shift.
static_assert(NOVA_CMD_ANSWER_MASK == (1U << NOVA_CMD_ANSWER_SHIFT) - 1);

// One object rather than a reserved physical range: the host maps
// exactly this much read-write, so its size bounds what a bridge can
// reach and `alignas` keeps anything else out of that mapping.
struct alignas(NOVA_CMD_PAGE) Page {
  std::array<unsigned char, NOVA_CMD_PAGE> byte{};
};

// One ring over one page. Default-constructed it is inert, which is
// what EL2 runs with before the page is placed — a drain finds nothing
// rather than faulting.
class Ring {
public:
  Ring() = default;

  explicit Ring(void* base) noexcept
      : widx_(reinterpret_cast<std::uint64_t*>(static_cast<char*>(base) + NOVA_CMD_WIDX_OFF)),
        ridx_(reinterpret_cast<std::uint64_t*>(static_cast<char*>(base) + NOVA_CMD_RIDX_OFF)),
        records_(reinterpret_cast<Record*>(static_cast<char*>(base) + NOVA_CMD_RECORDS_OFF)) {}

  [[nodiscard]] auto placed() const noexcept -> bool { return widx_ != nullptr; }

  // Producer side. False when full: the caller's cue to say so.
  [[nodiscard]] auto push(const Record& command) noexcept -> bool {
    if (!placed()) {
      return false;
    }
    const std::uint64_t write = std::atomic_ref{*widx_}.load(std::memory_order_relaxed); // producer-owned
    const std::uint64_t read  = std::atomic_ref{*ridx_}.load(std::memory_order_acquire);
    if (write - read >= NOVA_CMD_SLOTS) {
      return false;
    }
    Record& slot = records_[write % NOVA_CMD_SLOTS];
    slot.op      = command.op;
    slot.a       = command.a;
    slot.b       = command.b;
    std::atomic_ref{*widx_}.store(write + 1, std::memory_order_release); // publishes the body
    return true;
  }

  // Consumer side: hand every command written since the last drain to
  // `consume`, oldest first, and free their slots in one store.
  //
  // The bound is structural. `write` is read once on entry, so commands
  // arriving mid-drain wait for the next one; and a producer that has
  // run past the ring's depth has broken the protocol push() enforces,
  // so it is resynchronised to and nothing stale is executed. Neither
  // branch can make this callback longer than a ring.
  //
  // The count is how many commands this pass handed over — the same
  // number as the bound it computed. Discarding it is a decision the
  // caller states, like push()'s refusal, rather than one it can drop
  // by accident.
  template <typename Fn>
  [[nodiscard]] auto drain(Fn&& consume) noexcept -> std::size_t {
    if (!placed()) {
      return 0;
    }
    const std::uint64_t read  = std::atomic_ref{*ridx_}.load(std::memory_order_relaxed); // consumer-owned
    const std::uint64_t write = std::atomic_ref{*widx_}.load(std::memory_order_acquire); // publishes the bodies
    const auto          ahead = static_cast<std::int64_t>(write - read);
    if (ahead < 0 || ahead > NOVA_CMD_SLOTS) {
      std::atomic_ref{*ridx_}.store(write, std::memory_order_release);
      return 0;
    }
    for (std::uint64_t index = read; index != write; ++index) {
      consume(records_[index % NOVA_CMD_SLOTS]);
    }
    if (write != read) {
      std::atomic_ref{*ridx_}.store(write, std::memory_order_release); // frees the slots
    }
    return static_cast<std::size_t>(ahead);
  }

private:
  std::uint64_t* widx_    = nullptr;
  std::uint64_t* ridx_    = nullptr;
  Record*        records_ = nullptr;
};

// Lay out the page: geometry fields and both indices back to zero.
// Deliberately not the magic — that flag means "everything beside me is
// now true", and a producer sampling it early would write into indices
// about to be cleared.
inline void format(void* base, std::uint32_t period_us, const Limits& limits) noexcept {
  auto* header         = reinterpret_cast<Header*>(base);
  header->version      = NOVA_CMD_VERSION;
  header->record_size  = NOVA_CMD_REC_SIZE;
  header->slots        = NOVA_CMD_SLOTS;
  header->period_us    = period_us;
  header->slice_min_us = limits.slice_min_us;
  header->slice_def_us = limits.slice_def_us;
  header->slice_max_us = limits.slice_max_us;
  header->spi_lo       = limits.spi_lo;
  header->spi_hi       = limits.spi_hi;
  auto* write          = reinterpret_cast<std::uint64_t*>(static_cast<char*>(base) + NOVA_CMD_WIDX_OFF);
  auto* read           = reinterpret_cast<std::uint64_t*>(static_cast<char*>(base) + NOVA_CMD_RIDX_OFF);
  std::atomic_ref{*write}.store(0, std::memory_order_relaxed);
  std::atomic_ref{*read}.store(0, std::memory_order_relaxed);
}

// Make a formatted page findable. Last, so a page caught mid-format
// reads as absent rather than as a ring with plausible but wrong
// geometry — and so no command can be written before EL2 is able to
// take it.
inline void publish(void* base) noexcept {
  auto* header = reinterpret_cast<Header*>(base);
  std::atomic_ref{header->magic}.store(NOVA_CMD_MAGIC, std::memory_order_release);
}

// The page and the ring EL2 uses. Storage lives beside the model, like
// the trace rings, so the symbol a bridge resolves is a property of
// this layout rather than of whichever component places it.
inline Page g_page{};
inline Ring g_ring{};

// Bind the ring to the page and open it to the host. Runs once from the
// component's init, before the slot that drains it is armed;
// `period_us` is that slot's period, which is the wait being promised.
inline void place(std::uint32_t period_us, const Limits& limits) noexcept {
  format(g_page.byte.data(), period_us, limits);
  g_ring = Ring{g_page.byte.data()};
  publish(g_page.byte.data());
}

} // namespace nova::command
