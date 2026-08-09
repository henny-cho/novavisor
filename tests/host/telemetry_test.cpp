// Host-side tests for nova/telemetry.hpp — the publishing region, under
// real thread concurrency.
//
// The command ring's contract turned around again. There a producer
// writes and EL2 consumes; here EL2 writes and a reader that must never
// stall it consumes. So the questions are: can a reader ever assemble
// one value out of two readings, and does a sequence move for any
// reason other than the value moving. The second one is not a nicety —
// it is what lets a reader skip work it does not need, and a sequence
// that ticked every period would say "everything changed, always".

#include "nova/telemetry.hpp"

#include <array>
#include <atomic>
#include <cstdint>
#include <gtest/gtest.h>
#include <thread>
#include <vector>

namespace {

using nova::telemetry::Descriptor;
using nova::telemetry::Header;
using nova::telemetry::Publisher;
using nova::telemetry::Reading;
using nova::telemetry::Region;

constexpr std::uint32_t kPeriodUs = 10'000;
constexpr std::uint32_t kBudget   = NOVA_TLM_PAYLOAD_BYTES;
constexpr std::uint32_t kFreq     = 62'500'000;

// A source wide enough that a reader can tell a torn copy from a whole
// one: every word carries the same generation, so a reading with two
// different words is a reading of two moments.
constexpr std::size_t kWords = 64;

struct Source {
  std::array<std::uint64_t, kWords> word{};

  void set(std::uint64_t generation) noexcept {
    for (auto& value : word) {
      value = generation;
    }
  }
};

// One region, bound and opened, with the descriptor table reachable —
// the cases about what a reader sees need to look at a sequence the way
// the Python reader does, which is by offset and not through the
// publisher.
struct Fixture {
  Region    region{};
  Publisher publisher{region.byte.data(), kPeriodUs, kBudget, kFreq};

  auto base() noexcept -> void* { return region.byte.data(); }

  auto header() noexcept -> Header& { return *reinterpret_cast<Header*>(region.byte.data()); }

  auto descriptor(std::size_t index) noexcept -> Descriptor& {
    return reinterpret_cast<Descriptor*>(region.byte.data() + NOVA_TLM_DESCS_OFF)[index];
  }
};

TEST(TelemetryRegion, GeometryTravelsAndTheMagicComesLast) {
  Fixture fixture;
  Source  source{};

  // Before open() the region describes itself but does not offer
  // itself: a reader sampling here has to see "no region", not a slot
  // table that is still being filled.
  EXPECT_EQ(fixture.header().magic, 0U);
  EXPECT_EQ(fixture.header().version, NOVA_TLM_VERSION);
  EXPECT_EQ(fixture.header().period_us, kPeriodUs);
  EXPECT_EQ(fixture.header().budget, kBudget);
  EXPECT_EQ(fixture.header().freq, kFreq);
  EXPECT_EQ(fixture.header().slots, 0U);

  ASSERT_TRUE(fixture.publisher.declare(&source, sizeof source));
  fixture.publisher.open();

  EXPECT_EQ(fixture.header().magic, NOVA_TLM_MAGIC);
  EXPECT_EQ(fixture.header().slots, 1U);
  EXPECT_EQ(fixture.header().bytes, sizeof source);
  EXPECT_EQ(fixture.header().max_slots, NOVA_TLM_MAX_SLOTS);
  EXPECT_EQ(fixture.header().desc_size, NOVA_TLM_DESC_SIZE);
}

TEST(TelemetryRegion, ASlotIsFoundByTheAddressItCopies) {
  Fixture fixture;
  Source  first{};
  Source  second{};

  ASSERT_TRUE(fixture.publisher.declare(&first, sizeof first));
  ASSERT_TRUE(fixture.publisher.declare(&second, sizeof second));
  fixture.publisher.open();

  // The identity is the address, so the host's symbol resolution is the
  // whole of the lookup and no name table exists to disagree with it.
  EXPECT_EQ(fixture.descriptor(0).source, reinterpret_cast<std::uintptr_t>(&first));
  EXPECT_EQ(fixture.descriptor(1).source, reinterpret_cast<std::uintptr_t>(&second));
  // And payloads do not overlap.
  EXPECT_GE(fixture.descriptor(1).at, fixture.descriptor(0).at + fixture.descriptor(0).bytes);
}

TEST(TelemetryRegion, TheSequenceMovesWhenTheValueDoes) {
  Fixture fixture;
  Source  source{};
  source.set(1);
  ASSERT_TRUE(fixture.publisher.declare(&source, sizeof source));
  fixture.publisher.open();

  std::array<std::uint64_t, kWords> out{};
  const auto read = [&] { return nova::telemetry::read_slot(fixture.base(), 0, out.data(), sizeof out); };

  fixture.publisher.publish(100);
  const Reading first = read();
  ASSERT_TRUE(first.stable);
  EXPECT_EQ(out[0], 1U);
  EXPECT_EQ(first.stamp, 100U);

  // A turn over an unchanged value leaves the sequence and the stamp
  // alone. Otherwise every period would look like a change and the
  // gate would gate nothing.
  fixture.publisher.publish(200);
  fixture.publisher.publish(300);
  const Reading quiet = read();
  ASSERT_TRUE(quiet.stable);
  EXPECT_EQ(quiet.seq, first.seq);
  EXPECT_EQ(quiet.stamp, 100U);

  source.set(2);
  fixture.publisher.publish(400);
  const Reading moved = read();
  ASSERT_TRUE(moved.stable);
  EXPECT_NE(moved.seq, first.seq);
  EXPECT_EQ(moved.stamp, 400U);
  EXPECT_EQ(out[0], 2U);
}

TEST(TelemetryRegion, AReaderInsideTheWindowIsToldToRetry) {
  Fixture fixture;
  Source  source{};
  ASSERT_TRUE(fixture.publisher.declare(&source, sizeof source));
  fixture.publisher.open();
  fixture.publisher.publish(1);

  // What a reader arriving mid-copy sees. Reached by hand because the
  // publisher never leaves the window open on return — which is the
  // point, but leaves no way to observe the state from outside.
  fixture.descriptor(0).seq += 1;

  std::array<std::uint64_t, kWords> out{};
  EXPECT_FALSE(nova::telemetry::read_slot(fixture.base(), 0, out.data(), sizeof out).stable);
}

TEST(TelemetryRegion, ASlotTooBigForTheCallerIsRefusedNotTruncated) {
  Fixture fixture;
  Source  source{};
  ASSERT_TRUE(fixture.publisher.declare(&source, sizeof source));
  fixture.publisher.open();
  fixture.publisher.publish(1);

  std::array<std::uint64_t, kWords / 2> small{};
  // Handing back half a struct would decode as a plausible reading of
  // fields that were never read.
  EXPECT_FALSE(nova::telemetry::read_slot(fixture.base(), 0, small.data(), sizeof small).stable);
}

TEST(TelemetryRegion, SpansThatAreNotWholeWordsTravelWhole) {
  Fixture             fixture;
  std::uint16_t       narrow = 0;
  std::array<char, 3> odd{};

  // The manifest's smallest observations are two and four bytes wide,
  // and one is an array of an odd length. The copy's word path cannot
  // reach them, so this is the tail path and nothing else.
  ASSERT_TRUE(fixture.publisher.declare(&narrow, sizeof narrow));
  ASSERT_TRUE(fixture.publisher.declare(&odd, sizeof odd));
  fixture.publisher.open();

  narrow = 0xBEEF;
  odd    = {'a', 'b', 'c'};
  fixture.publisher.publish(7);

  std::uint16_t       out_narrow = 0;
  std::array<char, 3> out_odd{};
  ASSERT_TRUE(nova::telemetry::read_slot(fixture.base(), 0, &out_narrow, sizeof out_narrow).stable);
  ASSERT_TRUE(nova::telemetry::read_slot(fixture.base(), 1, out_odd.data(), sizeof out_odd).stable);
  EXPECT_EQ(out_narrow, 0xBEEF);
  EXPECT_EQ(out_odd, (std::array<char, 3>{'a', 'b', 'c'}));
}

TEST(TelemetryRegion, ABudgetSpreadsOneSweepOverSeveralTurns) {
  Region    region{};
  Source    first{};
  Source    second{};
  Publisher publisher{region.byte.data(), kPeriodUs, sizeof(Source), kFreq};

  ASSERT_TRUE(publisher.declare(&first, sizeof first));
  ASSERT_TRUE(publisher.declare(&second, sizeof second));
  publisher.open();

  first.set(1);
  second.set(1);

  // One slot fits the budget, so a turn takes one and the cursor holds
  // its place — the interrupt's cost is the budget rather than however
  // many slots happen to be registered.
  EXPECT_EQ(publisher.publish(10), sizeof(Source));
  std::array<std::uint64_t, kWords> out{};
  EXPECT_TRUE(nova::telemetry::read_slot(region.byte.data(), 0, out.data(), sizeof out).stable);
  EXPECT_EQ(out[0], 1U);
  EXPECT_EQ(nova::telemetry::read_slot(region.byte.data(), 1, out.data(), sizeof out).stamp, 0U);

  EXPECT_EQ(publisher.publish(20), sizeof(Source));
  EXPECT_EQ(nova::telemetry::read_slot(region.byte.data(), 1, out.data(), sizeof out).stamp, 20U);
}

TEST(TelemetryRegion, TheRegionRefusesWhatItCannotHold) {
  Region    region{};
  Publisher publisher{region.byte.data(), kPeriodUs, kBudget, kFreq};
  Source    source{};

  std::vector<Source> filler(NOVA_TLM_MAX_SLOTS);
  std::size_t         accepted = 0;
  for (auto& item : filler) {
    if (publisher.declare(&item, sizeof item)) {
      ++accepted;
    }
  }
  // Whichever ceiling is reached first, the answer is no rather than a
  // payload written over a neighbour's.
  EXPECT_LT(accepted, filler.size() + 1);
  EXPECT_EQ(publisher.slots(), accepted);
  const bool room = accepted < NOVA_TLM_MAX_SLOTS && (accepted + 1) * sizeof(Source) <= NOVA_TLM_PAYLOAD_BYTES;
  EXPECT_EQ(publisher.declare(&source, sizeof source), room);
  EXPECT_FALSE(publisher.declare(&source, 0));
}

TEST(TelemetryRegion, AnUnplacedPublisherIsInert) {
  Publisher publisher{};
  Source    source{};
  EXPECT_FALSE(publisher.placed());
  EXPECT_FALSE(publisher.declare(&source, sizeof source));
  EXPECT_EQ(publisher.publish(1), 0U);
  publisher.open();
}

// The whole point, under threads. A writer keeps bumping a generation
// while a reader keeps reading; every reading the reader accepts must
// be one generation, never a mix of two.
TEST(TelemetryRegion, NoAcceptedReadingIsEverAMixOfTwo) {
  Fixture           fixture;
  Source            source{};
  std::atomic<bool> running{true};

  ASSERT_TRUE(fixture.publisher.declare(&source, sizeof source));
  fixture.publisher.open();

  std::thread writer([&] {
    for (std::uint64_t generation = 1; running.load(std::memory_order_relaxed); ++generation) {
      source.set(generation);
      fixture.publisher.publish(generation);
    }
  });

  std::size_t accepted = 0;
  std::size_t retried  = 0;
  for (int attempt = 0; attempt < 200'000; ++attempt) {
    std::array<std::uint64_t, kWords> out{};
    const Reading                     reading = nova::telemetry::read_slot(fixture.base(), 0, out.data(), sizeof out);
    if (!reading.stable) {
      ++retried;
      continue;
    }
    ++accepted;
    for (const auto value : out) {
      ASSERT_EQ(value, out[0]) << "attempt " << attempt << " assembled two readings";
    }
  }
  running.store(false, std::memory_order_relaxed);
  writer.join();

  // The reader has to have got through, or the case proved nothing —
  // a seqlock that always says "retry" would pass the loop above.
  EXPECT_GT(accepted, 0U);
  EXPECT_EQ(accepted + retried, 200'000U);
}

} // namespace
