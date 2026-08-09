// Host-side tests for nova/command.hpp — the refusing ring, under real
// thread concurrency.
//
// The trace ring's contract turned around: there the writer must never
// be stopped by the reader and loss is the price, here a command that
// was accepted must arrive exactly once and in order and one that
// cannot be accepted must be told so. So these cases are about what the
// ring refuses as much as what it carries — and about what a consumer
// does when the producer is not following the protocol at all.

#include "nova/command.hpp"

#include <atomic>
#include <cstdint>
#include <gtest/gtest.h>
#include <thread>
#include <vector>

namespace {

using nova::command::Page;
using nova::command::Record;
using nova::command::Ring;

// The drain period a placed page would carry. Any value: these tests
// are about the protocol, and the number only has to survive format().
constexpr std::uint32_t kPeriodUs = 10'000;

// One page, formatted and published, with the two indices reachable —
// the tests that break the protocol need to write `widx` the way a
// broken producer would, which is by address and not through push().
struct Fixture {
  Page page{};

  Fixture() {
    nova::command::format(base(), kPeriodUs, {});
    nova::command::publish(base());
  }

  auto base() noexcept -> void* { return page.byte.data(); }
  auto ring() noexcept -> Ring { return Ring{base()}; }
  auto at(std::size_t offset) noexcept -> std::uint64_t& {
    return *reinterpret_cast<std::uint64_t*>(page.byte.data() + offset);
  }
  auto widx() noexcept -> std::uint64_t& { return at(NOVA_CMD_WIDX_OFF); }
  auto ridx() noexcept -> std::uint64_t& { return at(NOVA_CMD_RIDX_OFF); }
};

// Everything a drain handed over, in the order it arrived.
auto take(Ring& ring) -> std::vector<Record> {
  std::vector<Record> got;
  ring.drain([&got](const Record& command) { got.push_back(command); });
  return got;
}

auto command(std::uint64_t op, std::uint64_t a = 0, std::uint64_t b = 0) -> Record {
  return Record{op, a, b};
}

TEST(CommandRingFormat, GeometryIsPublishedAndTheMagicComesLast) {
  Page page{};
  nova::command::format(page.byte.data(), kPeriodUs, {});

  auto* header = reinterpret_cast<nova::command::Header*>(page.byte.data());
  EXPECT_EQ(header->version, NOVA_CMD_VERSION);
  EXPECT_EQ(header->record_size, NOVA_CMD_REC_SIZE);
  EXPECT_EQ(header->slots, NOVA_CMD_SLOTS);
  // The wait EL2 promises, read by the host rather than assumed.
  EXPECT_EQ(header->period_us, kPeriodUs);
  // A page caught between the two reads as absent, so nothing can write
  // a command into indices that are about to be cleared.
  EXPECT_EQ(header->magic, 0U);

  nova::command::publish(page.byte.data());
  EXPECT_EQ(header->magic, NOVA_CMD_MAGIC);
}

TEST(CommandRing, AnUnplacedRingRefusesRatherThanFaults) {
  Ring ring;
  EXPECT_FALSE(ring.placed());
  EXPECT_FALSE(ring.push(command(NOVA_CMD_OP_MARK)));
  auto got = take(ring);
  EXPECT_TRUE(got.empty());
}

TEST(CommandRing, CommandsArriveOnceInOrderWithTheirArguments) {
  Fixture fixture;
  Ring    ring = fixture.ring();

  ASSERT_TRUE(ring.push(command(NOVA_CMD_OP_MARK, 7, 9)));
  ASSERT_TRUE(ring.push(command(NOVA_CMD_OP_SPI, 1, 42)));

  auto got = take(ring);
  ASSERT_EQ(got.size(), 2U);
  EXPECT_EQ(got[0].op, NOVA_CMD_OP_MARK);
  EXPECT_EQ(got[0].a, 7U);
  EXPECT_EQ(got[0].b, 9U);
  EXPECT_EQ(got[1].op, NOVA_CMD_OP_SPI);
  EXPECT_EQ(got[1].a, 1U);
  EXPECT_EQ(got[1].b, 42U);

  // Draining is not re-reading: a second pass finds nothing.
  EXPECT_TRUE(take(ring).empty());
}

TEST(CommandRing, AFullRingRefusesAndKeepsWhatItAlreadyHas) {
  Fixture fixture;
  Ring    ring = fixture.ring();

  for (std::uint64_t index = 0; index < NOVA_CMD_SLOTS; ++index) {
    ASSERT_TRUE(ring.push(command(NOVA_CMD_OP_MARK, index)));
  }
  // The refusal is the contract: the ring is at depth, and the command
  // that did not fit does not silently replace one that did.
  EXPECT_FALSE(ring.push(command(NOVA_CMD_OP_MARK, 0xDEAD)));

  auto got = take(ring);
  ASSERT_EQ(got.size(), std::size_t{NOVA_CMD_SLOTS});
  for (std::size_t index = 0; index < got.size(); ++index) {
    EXPECT_EQ(got[index].a, index);
  }
  // And a drained ring accepts again — the refusal was about depth, not
  // about the ring being spent.
  EXPECT_TRUE(ring.push(command(NOVA_CMD_OP_MARK, 0xBEEF)));
}

TEST(CommandRing, IndicesWrapWithoutDisturbingOrder) {
  Fixture fixture;
  Ring    ring = fixture.ring();

  // Several times the depth, so every slot is reused and the modulo is
  // exercised rather than assumed.
  std::uint64_t next = 0;
  for (int round = 0; round < 5; ++round) {
    for (std::uint64_t index = 0; index < NOVA_CMD_SLOTS; ++index) {
      ASSERT_TRUE(ring.push(command(NOVA_CMD_OP_MARK, next++)));
    }
    auto got = take(ring);
    ASSERT_EQ(got.size(), std::size_t{NOVA_CMD_SLOTS});
    EXPECT_EQ(got.front().a, next - NOVA_CMD_SLOTS);
    EXPECT_EQ(got.back().a, next - 1);
  }
  EXPECT_EQ(fixture.widx(), next);
  EXPECT_EQ(fixture.ridx(), next);
}

TEST(CommandRing, ADrainStopsAtTheIndexItReadOnEntry) {
  Fixture fixture;
  Ring    ring = fixture.ring();

  ASSERT_TRUE(ring.push(command(NOVA_CMD_OP_MARK, 1)));
  std::vector<std::uint64_t> seen;
  ring.drain([&](const Record& taken) {
    seen.push_back(taken.a);
    // What a producer racing this drain does. The bound is what keeps a
    // timer callback from being lengthened by a host that keeps writing.
    if (taken.a < 4) {
      EXPECT_TRUE(ring.push(command(NOVA_CMD_OP_MARK, taken.a + 1)));
    }
  });
  EXPECT_EQ(seen, std::vector<std::uint64_t>{1});
  // Not lost, only deferred: the next drain takes what arrived during
  // this one.
  auto got = take(ring);
  ASSERT_EQ(got.size(), 1U);
  EXPECT_EQ(got[0].a, 2U);
}

TEST(CommandRing, AProducerPastTheDepthIsResynchronisedNotObeyed) {
  Fixture fixture;
  Ring    ring = fixture.ring();

  // push() cannot produce this; only a broken or hostile writer can.
  // Executing what the slots happen to hold would be acting on stale
  // records, so the pass consumes nothing and catches up instead.
  fixture.widx() = NOVA_CMD_SLOTS + 1;
  EXPECT_TRUE(take(ring).empty());
  EXPECT_EQ(fixture.ridx(), fixture.widx());

  // A producer that goes backwards — a fresh bridge that kept its own
  // count — reads the same way and is answered the same way.
  fixture.widx() = 1;
  EXPECT_TRUE(take(ring).empty());
  EXPECT_EQ(fixture.ridx(), 1U);

  // Recovered: the ring works from wherever the two agreed again.
  ASSERT_TRUE(ring.push(command(NOVA_CMD_OP_SLICE, 500)));
  auto got = take(ring);
  ASSERT_EQ(got.size(), 1U);
  EXPECT_EQ(got[0].op, NOVA_CMD_OP_SLICE);
  EXPECT_EQ(got[0].a, 500U);
}

TEST(CommandRing, EveryAcceptedCommandArrivesExactlyOnceUnderConcurrency) {
  // The property the ordering protocol exists for, checked against a
  // producer and a consumer on separate cores: what push() said yes to
  // is delivered whole, once, in the order it was accepted. A refusal
  // is not a loss — it is retried, which is what a bridge does.
  constexpr std::uint64_t kCommands = 20000;

  Fixture           fixture;
  Ring              producer = fixture.ring();
  Ring              consumer = fixture.ring();
  std::atomic<bool> writing{true};

  std::thread writer([&] {
    for (std::uint64_t index = 0; index < kCommands; ++index) {
      while (!producer.push(command(NOVA_CMD_OP_MARK, index, index * 3))) {
        std::this_thread::yield();
      }
    }
    writing.store(false, std::memory_order_release);
  });

  std::vector<Record> got;
  got.reserve(kCommands);
  while (writing.load(std::memory_order_acquire) || got.size() < kCommands) {
    consumer.drain([&got](const Record& taken) { got.push_back(taken); });
  }
  writer.join();

  ASSERT_EQ(got.size(), kCommands);
  for (std::uint64_t index = 0; index < kCommands; ++index) {
    EXPECT_EQ(got[index].op, NOVA_CMD_OP_MARK);
    EXPECT_EQ(got[index].a, index);
    // The second word travels with the first. A torn record would show
    // up here as a pair that was never written together.
    EXPECT_EQ(got[index].b, index * 3);
  }
}

} // namespace
