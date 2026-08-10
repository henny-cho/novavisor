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
#include <span>

namespace nova {

// Carried by pointer only, like core_gic.hpp does it: an incomplete
// type is how the compiler guarantees nothing here touches the frame.
struct TrapContext;

} // namespace nova

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

struct Header {
  std::uint64_t magic       = 0;
  std::uint32_t version     = 0;
  std::uint32_t record_size = 0;
  std::uint32_t slots       = 0;
  std::uint32_t period_us   = 0;
  std::uint32_t rows        = 0;
  std::uint32_t row_size    = 0;
};

static_assert(offsetof(Header, magic) == NOVA_CMD_MAGIC_OFF);
static_assert(offsetof(Header, version) == NOVA_CMD_VERSION_OFF);
static_assert(offsetof(Header, record_size) == NOVA_CMD_RECSIZE_OFF);
static_assert(offsetof(Header, slots) == NOVA_CMD_SLOTS_OFF);
static_assert(offsetof(Header, period_us) == NOVA_CMD_PERIOD_OFF);
static_assert(offsetof(Header, rows) == NOVA_CMD_NROWS_OFF);
static_assert(offsetof(Header, row_size) == NOVA_CMD_ROWSZ_OFF);
static_assert(sizeof(Header) <= NOVA_CMD_WIDX_OFF);

// One published row: what an op accepts, in the page. Mirrored from the
// ABI like Record and Header are, so a moved field stops the build here
// rather than shifting what a host reads.
struct Row {
  std::uint16_t op     = 0;
  std::uint8_t  words  = 0;
  std::uint8_t  a_kind = 0;
  std::uint8_t  b_kind = 0;
  // No padding member: the alignment the bands need puts it there, and
  // the offsets below are what says where everything landed.
  std::uint32_t a_lo  = 0;
  std::uint32_t a_hi  = 0;
  std::uint32_t a_def = 0;
  std::uint32_t b_lo  = 0;
  std::uint32_t b_hi  = 0;
  std::uint32_t b_def = 0;
};

static_assert(sizeof(Row) == NOVA_CMD_OPS_ROW);
static_assert(offsetof(Row, op) == NOVA_CMD_ROW_OP_OFF);
static_assert(offsetof(Row, words) == NOVA_CMD_ROW_WORDS_OFF);
static_assert(offsetof(Row, a_kind) == NOVA_CMD_ROW_AKIND_OFF);
static_assert(offsetof(Row, b_kind) == NOVA_CMD_ROW_BKIND_OFF);
static_assert(offsetof(Row, a_lo) == NOVA_CMD_ROW_A_OFF);
static_assert(offsetof(Row, b_lo) == NOVA_CMD_ROW_B_OFF);
static_assert(NOVA_CMD_OPS_OFF + NOVA_CMD_OPS_CAP * NOVA_CMD_OPS_ROW == NOVA_CMD_RECORDS_OFF,
              "the rows end where the records begin");

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

// What one argument word means and what it accepts, declared by
// whoever reads it. `kind` is the argument's meaning, not its width: a
// reader offers a VM index as this run's guests and microseconds as a
// duration. lo > hi is a free argument — any value the op takes.
struct Arg {
  std::uint8_t  kind = NOVA_CMD_ARG_PLAIN;
  std::uint32_t lo   = 1;
  std::uint32_t hi   = 0;
  std::uint32_t def  = 0;
};

// One op this build carries out: the record's shape, plus the code that
// carries it out. Whoever declares it owns both the band advertised and
// the check enforcing it, so the two cannot part.
using Handler = std::uint64_t (*)(const Record&, TrapContext*) noexcept;

struct Op {
  std::uint16_t op    = 0;
  std::uint8_t  words = 0; // how many of a, b this op gives meaning to
  Arg           a{};
  Arg           b{};
  Handler       run = nullptr;
};

// The ops one build carries out. Dispatch walks it and place() projects
// it into the page, so what is advertised and what is accepted are one
// table asked two ways.
class OpTable {
public:
  // False when the table is full or the opcode is already claimed. The
  // caller counts refusals rather than deciding what to do about one.
  [[nodiscard]] auto declare(const Op& entry) noexcept -> bool {
    if (count_ == row_.size() || entry.op == 0 || entry.run == nullptr || find(entry.op) != nullptr) {
      return false;
    }
    row_[count_++] = entry;
    return true;
  }

  [[nodiscard]] auto dispatch(const Record& command, TrapContext* ctx) const noexcept -> std::uint64_t {
    if (command.op > NOVA_CMD_ANSWER_MASK) {
      return NOVA_CMD_RESULT_UNKNOWN; // too wide to name, so nothing claims it
    }
    const Op* entry = find(static_cast<std::uint16_t>(command.op));
    return entry == nullptr ? NOVA_CMD_RESULT_UNKNOWN : entry->run(command, ctx);
  }

  [[nodiscard]] auto entries() const noexcept -> std::span<const Op> { return {row_.data(), count_}; }

private:
  [[nodiscard]] auto find(std::uint16_t op) const noexcept -> const Op* {
    for (std::size_t i = 0; i < count_; ++i) {
      if (row_[i].op == op) {
        return &row_[i];
      }
    }
    return nullptr;
  }

  std::array<Op, NOVA_CMD_OPS_CAP> row_{}; // the page's capacity, not a second number
  std::size_t                      count_ = 0;
};

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

  // Whether anything outside the guests has ever driven this machine.
  // Not "is a host connected" — nothing here can know that — but the
  // question a halt decision actually has: with every vCPU off, is
  // there still someone who could start one?
  //
  // The producer owns this index and is not trusted, which bounds what
  // a false answer buys: the machine idles instead of halting. It
  // cannot make anything run.
  [[nodiscard]] auto spoken_to() const noexcept -> bool {
    return placed() && std::atomic_ref{*widx_}.load(std::memory_order_relaxed) != 0;
  }

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

// Lay out the page: geometry, the rows this build carries out, and both
// indices back to zero. Deliberately not the magic — that flag means
// "everything beside me is now true", and a producer sampling it early
// would write into indices about to be cleared.
//
// Fields one at a time, no memcpy: EL2 stays FP-free and the libc mem*
// routines reach for SIMD.
inline void format(void* base, std::uint32_t period_us, const OpTable& ops) noexcept {
  auto* header        = reinterpret_cast<Header*>(base);
  header->version     = NOVA_CMD_VERSION;
  header->record_size = NOVA_CMD_REC_SIZE;
  header->slots       = NOVA_CMD_SLOTS;
  header->period_us   = period_us;
  header->row_size    = NOVA_CMD_OPS_ROW;

  auto*         row   = reinterpret_cast<Row*>(static_cast<char*>(base) + NOVA_CMD_OPS_OFF);
  std::uint32_t count = 0;
  for (const Op& entry : ops.entries()) {
    row[count].op     = entry.op;
    row[count].words  = entry.words;
    row[count].a_kind = entry.a.kind;
    row[count].b_kind = entry.b.kind;
    row[count].a_lo   = entry.a.lo;
    row[count].a_hi   = entry.a.hi;
    row[count].a_def  = entry.a.def;
    row[count].b_lo   = entry.b.lo;
    row[count].b_hi   = entry.b.hi;
    row[count].b_def  = entry.b.def;
    ++count;
  }
  header->rows = count;

  auto* write = reinterpret_cast<std::uint64_t*>(static_cast<char*>(base) + NOVA_CMD_WIDX_OFF);
  auto* read  = reinterpret_cast<std::uint64_t*>(static_cast<char*>(base) + NOVA_CMD_RIDX_OFF);
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
// component's init, after the ops are collected and before the slot
// that drains them is armed; `period_us` is that slot's period, which
// is the wait being promised.
inline void place(std::uint32_t period_us, const OpTable& ops) noexcept {
  format(g_page.byte.data(), period_us, ops);
  g_ring = Ring{g_page.byte.data()};
  publish(g_page.byte.data());
}

} // namespace nova::command
