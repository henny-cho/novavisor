// Host-side tests for ivc/ring.hpp — the SPSC protocol under real
// thread concurrency. The guest C helper (demo/common/guest_ring.h)
// implements the identical layout and ordering, so what these threads
// prove holds between two cores.

#include "ivc/ring.hpp"

#include <array>
#include <cstdint>
#include <gtest/gtest.h>
#include <thread>

namespace {

// A fake shared page region: one ring's worth of header + slots.
struct alignas(64) RingStorage {
  std::array<unsigned char, NOVA_IVC_RING_SLOTS_OFF + NOVA_IVC_RING_SLOTS * 8> bytes{};
};

TEST(IvcRing, StartsEmpty) {
  RingStorage     mem;
  nova::ivc::Ring ring{mem.bytes.data()};
  std::uint64_t   v = 0;
  EXPECT_FALSE(ring.pop(v));
}

TEST(IvcRing, PushPopRoundTrips) {
  RingStorage     mem;
  nova::ivc::Ring ring{mem.bytes.data()};
  ASSERT_TRUE(ring.push(0xABCDULL));
  std::uint64_t v = 0;
  ASSERT_TRUE(ring.pop(v));
  EXPECT_EQ(v, 0xABCDULL);
  EXPECT_FALSE(ring.pop(v)); // drained
}

TEST(IvcRing, FullRingRejectsUntilPopped) {
  RingStorage     mem;
  nova::ivc::Ring ring{mem.bytes.data()};
  for (std::uint64_t i = 0; i < NOVA_IVC_RING_SLOTS; ++i) {
    ASSERT_TRUE(ring.push(i));
  }
  EXPECT_FALSE(ring.push(999));
  std::uint64_t v = 0;
  ASSERT_TRUE(ring.pop(v));
  EXPECT_EQ(v, 0U);
  EXPECT_TRUE(ring.push(999)); // one slot freed
}

TEST(IvcRing, IndicesSurviveU32Wraparound) {
  RingStorage     mem;
  nova::ivc::Ring ring{mem.bytes.data()};
  // Preload both indices near the u32 limit — monotonic indices must
  // keep working across the wrap (power-of-two slot count).
  const std::uint32_t near_wrap                                                = ~0U - 3;
  *reinterpret_cast<std::uint32_t*>(mem.bytes.data() + NOVA_IVC_RING_WIDX_OFF) = near_wrap;
  *reinterpret_cast<std::uint32_t*>(mem.bytes.data() + NOVA_IVC_RING_RIDX_OFF) = near_wrap;
  for (std::uint64_t i = 0; i < 8; ++i) {
    ASSERT_TRUE(ring.push(i));
    std::uint64_t v = ~0ULL;
    ASSERT_TRUE(ring.pop(v));
    EXPECT_EQ(v, i);
  }
}

TEST(IvcRing, SpscConcurrentSequenceIntact) {
  RingStorage     mem;
  nova::ivc::Ring ring{mem.bytes.data()};

  constexpr std::uint64_t kCount = 200'000;

  std::thread producer{[&] {
    for (std::uint64_t i = 0; i < kCount; ++i) {
      while (!ring.push(i)) {
        // full — consumer is behind
      }
    }
  }};

  std::uint64_t received = 0;
  bool          in_order = true;
  while (received < kCount) {
    std::uint64_t v = 0;
    if (ring.pop(v)) {
      in_order = in_order && v == received;
      ++received;
    }
  }
  producer.join();

  EXPECT_TRUE(in_order); // every value arrived exactly once, in order
}

} // namespace
