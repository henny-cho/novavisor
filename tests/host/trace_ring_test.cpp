// Host-side tests for nova/trace.hpp — the overwriting ring, under real
// thread concurrency.
//
// The contract this has to prove is unusual: the writer must never be
// slowed or stopped by the reader, so loss is allowed. What is *not*
// allowed is loss the reader cannot detect, or a record it reads as
// whole when it was half written. Both are checked below against a
// writer running flat out on another thread.

#include "nova/trace.hpp"

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <gtest/gtest.h>
#include <thread>
#include <vector>

namespace {

using nova::trace::Header;
using nova::trace::Record;
using nova::trace::Ring;

// One region's worth of bytes, aligned like the real reserved page.
template <std::size_t Rings>
struct alignas(64) Region {
  alignas(64) std::array<unsigned char, nova::trace::region_size(Rings)> bytes{};

  auto base() noexcept -> void* { return bytes.data(); }
  auto header() noexcept -> Header* { return reinterpret_cast<Header*>(bytes.data()); }
  auto ring(std::size_t index) noexcept -> Ring { return Ring{nova::trace::ring_at(base(), index)}; }
};

// What a reader does: take head, copy the live window, take head again,
// and keep only what did not fall out of the window meanwhile.
struct Drain {
  std::vector<Record> records;
  std::uint64_t       lost = 0;
  std::uint64_t       head = 0;
};

auto drain(void* base, std::size_t index, std::uint64_t cursor) -> Drain {
  auto* ring    = static_cast<char*>(nova::trace::ring_at(base, index));
  auto* head    = reinterpret_cast<std::uint64_t*>(ring + NOVA_TRACE_HEAD_OFF);
  auto* records = reinterpret_cast<Record*>(ring + NOVA_TRACE_RECORDS_OFF);

  Drain               out;
  const std::uint64_t before = std::atomic_ref{*head}.load(std::memory_order_acquire);
  const std::uint64_t oldest = before > NOVA_TRACE_CAPACITY ? before - NOVA_TRACE_CAPACITY : 0;
  const std::uint64_t from   = cursor > oldest ? cursor : oldest;
  for (std::uint64_t at = from; at < before; ++at) {
    out.records.push_back(records[at & nova::trace::kCapacityMask]);
  }
  // Anything the writer lapped during the copy is discarded, not
  // trusted: re-reading head is what makes that decidable.
  const std::uint64_t after = std::atomic_ref{*head}.load(std::memory_order_acquire);
  const std::uint64_t safe  = after > NOVA_TRACE_CAPACITY ? after - NOVA_TRACE_CAPACITY : 0;
  if (safe > from) {
    const auto drop = static_cast<std::size_t>(std::min<std::uint64_t>(safe - from, out.records.size()));
    out.records.erase(out.records.begin(), out.records.begin() + static_cast<long>(drop));
  }
  out.head = before;
  // Derived, never accumulated: the cursor advanced over `before -
  // cursor` records and kept some of them, so the rest are the loss by
  // definition. Counting the two skipped spans separately double-counts
  // wherever they overlap, which is exactly what a lapping writer makes
  // them do.
  out.lost = (before - cursor) - out.records.size();
  return out;
}

TEST(TraceRing, AnUnplacedRingCountsRatherThanFaults) {
  Ring ring;
  EXPECT_FALSE(ring.placed());
  const std::uint32_t before = nova::trace::g_early.load(std::memory_order_relaxed);
  ring.emit(1, NOVA_TRACE_EV_TRAP, 0, 2, 3, 4); // must not crash
  EXPECT_EQ(ring.head(), 0U);
  // The event is gone — there is nowhere for it to go — but it is not
  // gone silently. A loss nobody counts reads as nothing happening.
  EXPECT_EQ(nova::trace::g_early.load(std::memory_order_relaxed), before + 1);
}

TEST(TraceRing, PlacementPublishesWhatWasLostBeforeIt) {
  Region<1> region;
  nova::trace::g_early.store(0, std::memory_order_relaxed);
  // Three events with no ring to land in, then the region arrives.
  for (int i = 0; i < 3; ++i) {
    nova::trace::g_ring[0].emit(1, NOVA_TRACE_EV_TRAP, 0, 0, 0, 0);
  }
  nova::trace::place(region.base(), 1, 1'000'000);

  EXPECT_EQ(region.header()->early, 3U);

  // And from here the same emit lands, so the counter stops moving.
  nova::trace::g_ring[0].emit(9, NOVA_TRACE_EV_TRAP, 0, 0, 0, 0);
  EXPECT_EQ(nova::trace::g_early.load(std::memory_order_relaxed), 3U);
  EXPECT_EQ(drain(region.base(), 0, 0).records.size(), 1U);

  // g_ring is inline storage shared by the whole binary: leave it inert
  // so a later test does not write into this stack region.
  nova::trace::g_ring[0] = Ring{};
}

TEST(TraceRing, AFormattedRegionIsNotFindableUntilPublished) {
  // The magic is the promise that everything beside it is true, and one
  // field is not true until the rings are bound. A reader that found
  // the region in between would cache a zero for it.
  Region<2> region;
  nova::trace::format(region.base(), 2, 62'500'000);
  EXPECT_NE(region.header()->magic, NOVA_TRACE_MAGIC);
  EXPECT_EQ(region.header()->stride, nova::trace::kRingStride);

  nova::trace::publish(region.base(), 7);
  EXPECT_EQ(region.header()->magic, NOVA_TRACE_MAGIC);
  EXPECT_EQ(region.header()->early, 7U);
}

TEST(TraceRing, FormatPublishesGeometryAndMagicLast) {
  Region<2> region;
  nova::trace::format(region.base(), 2, 62'500'000);
  nova::trace::publish(region.base(), 0);

  const Header* header = region.header();
  EXPECT_EQ(header->magic, NOVA_TRACE_MAGIC);
  EXPECT_EQ(header->version, NOVA_TRACE_VERSION);
  EXPECT_EQ(header->record_size, NOVA_TRACE_REC_SIZE);
  EXPECT_EQ(header->capacity, NOVA_TRACE_CAPACITY);
  EXPECT_EQ(header->rings, 2U);
  // ts -> seconds has one source, and it travels with the geometry.
  EXPECT_EQ(header->freq_hz, 62'500'000U);
  EXPECT_EQ(header->stride, nova::trace::kRingStride);
}

TEST(TraceRing, EmitRoundTrips) {
  Region<1> region;
  nova::trace::format(region.base(), 1, 1'000'000);
  Ring ring = region.ring(0);

  ring.emit(0x1122334455667788ULL, NOVA_TRACE_EV_VGIC_BIND, 1, 37, 0x25, 2);

  const Drain out = drain(region.base(), 0, 0);
  ASSERT_EQ(out.records.size(), 1U);
  EXPECT_EQ(out.records[0].ts, 0x1122334455667788ULL);
  EXPECT_EQ(out.records[0].type, NOVA_TRACE_EV_VGIC_BIND);
  EXPECT_EQ(out.records[0].cpu, 1);
  EXPECT_EQ(out.records[0].a, 37U);
  EXPECT_EQ(out.records[0].b, 0x25U);
  EXPECT_EQ(out.records[0].c, 2U);
  EXPECT_EQ(out.lost, 0U);
}

TEST(TraceRing, RingsAreIndependent) {
  Region<2> region;
  nova::trace::format(region.base(), 2, 1'000'000);
  region.ring(0).emit(10, NOVA_TRACE_EV_TRAP, 0, 1, 0, 0);
  region.ring(1).emit(20, NOVA_TRACE_EV_MMIO, 1, 2, 0, 0);

  EXPECT_EQ(drain(region.base(), 0, 0).records.size(), 1U);
  EXPECT_EQ(drain(region.base(), 1, 0).records.size(), 1U);
  EXPECT_EQ(drain(region.base(), 0, 0).records[0].type, NOVA_TRACE_EV_TRAP);
  EXPECT_EQ(drain(region.base(), 1, 0).records[0].type, NOVA_TRACE_EV_MMIO);
}

TEST(TraceRing, TheWriterNeverStopsAndTheReaderIsToldWhatItMissed) {
  Region<1> region;
  nova::trace::format(region.base(), 1, 1'000'000);
  Ring ring = region.ring(0);

  // Three times the ring: the writer laps twice and does not care.
  const std::uint64_t total = NOVA_TRACE_CAPACITY * 3;
  for (std::uint64_t index = 0; index < total; ++index) {
    ring.emit(index, NOVA_TRACE_EV_TRAP, 0, static_cast<std::uint32_t>(index), 0, 0);
  }
  EXPECT_EQ(ring.head(), total);

  const Drain out = drain(region.base(), 0, 0);
  // Exactly the last capacity records survive, and the count of the
  // rest is reported rather than silently absent.
  EXPECT_EQ(out.records.size(), std::size_t{NOVA_TRACE_CAPACITY});
  EXPECT_EQ(out.lost, total - NOVA_TRACE_CAPACITY);
  EXPECT_EQ(out.records.front().ts, total - NOVA_TRACE_CAPACITY);
  EXPECT_EQ(out.records.back().ts, total - 1);
}

TEST(TraceRing, IncrementalDrainsLoseNothingWhenKeepingUp) {
  Region<1> region;
  nova::trace::format(region.base(), 1, 1'000'000);
  Ring ring = region.ring(0);

  std::uint64_t cursor = 0;
  std::uint64_t seen   = 0;
  for (int round = 0; round < 8; ++round) {
    for (int i = 0; i < 100; ++i) {
      ring.emit(cursor + static_cast<std::uint64_t>(i), NOVA_TRACE_EV_TRAP, 0, 0, 0, 0);
    }
    const Drain out = drain(region.base(), 0, cursor);
    EXPECT_EQ(out.lost, 0U);
    seen += out.records.size();
    cursor = out.head;
  }
  EXPECT_EQ(seen, 800U);
}

TEST(TraceRing, ConcurrentWriterNeverYieldsATornRecord) {
  // Every field is derived from the same counter, so a record that
  // mixes two events is detectable by arithmetic alone. The writer runs
  // flat out and is expected to lap the reader many times over — the
  // claim is not that nothing is lost, but that nothing read is wrong.
  Region<1> region;
  nova::trace::format(region.base(), 1, 1'000'000);
  Ring ring = region.ring(0);

  constexpr std::uint64_t kEvents = 400'000;
  std::atomic<bool>       done{false};
  std::thread             writer([&] {
    for (std::uint64_t index = 1; index <= kEvents; ++index) {
      ring.emit(index, NOVA_TRACE_EV_VGIC_BIND, static_cast<std::uint8_t>(index & 1),
                            static_cast<std::uint32_t>(index & 0xFFFFFFFFU), index * 2, index * 3);
    }
    done.store(true, std::memory_order_release);
  });

  std::uint64_t cursor  = 0;
  std::uint64_t checked = 0;
  std::uint64_t lost    = 0;
  while (!done.load(std::memory_order_acquire) || cursor < ring.head()) {
    const Drain out = drain(region.base(), 0, cursor);
    for (const Record& record : out.records) {
      ASSERT_EQ(record.b, record.ts * 2) << "torn record at ts " << record.ts;
      ASSERT_EQ(record.c, record.ts * 3) << "torn record at ts " << record.ts;
      ASSERT_EQ(record.cpu, record.ts & 1) << "torn record at ts " << record.ts;
      ASSERT_EQ(record.type, NOVA_TRACE_EV_VGIC_BIND);
    }
    checked += out.records.size();
    lost += out.lost;
    cursor = out.head;
  }
  writer.join();

  EXPECT_EQ(ring.head(), kEvents);
  // Whatever the split between the two, they must account for the lot:
  // a reader that quietly skipped records would show up right here.
  EXPECT_EQ(checked + lost, kEvents);
  EXPECT_GT(checked, 0U);
}

} // namespace
