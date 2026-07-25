// Host-side GTest suite for the legacy one-shot claim policy
// (components/core_timer/include/core_timer/legacy_slot_model.hpp).

#include "core_timer/legacy_slot_model.hpp"

#include <gtest/gtest.h>

namespace {

using nova::core_timer::LegacySlot;
using nova::core_timer::release;
using nova::core_timer::try_claim;

TEST(LegacySlotModel, FreshSlotIsUnarmed) {
  const LegacySlot slot{};
  EXPECT_FALSE(slot.armed);
}

TEST(LegacySlotModel, FreeSlotAcceptsAndStampsOwner) {
  LegacySlot slot{};
  EXPECT_TRUE(try_claim(slot, 3));
  EXPECT_TRUE(slot.armed);
  EXPECT_EQ(slot.owner, 3U);
}

TEST(LegacySlotModel, OwnerCanRearm) {
  LegacySlot slot{};
  ASSERT_TRUE(try_claim(slot, 3));
  EXPECT_TRUE(try_claim(slot, 3));
  EXPECT_EQ(slot.owner, 3U);
}

TEST(LegacySlotModel, ForeignClaimDeniedAndLeavesOwnerIntact) {
  LegacySlot slot{};
  ASSERT_TRUE(try_claim(slot, 3));
  EXPECT_FALSE(try_claim(slot, 5));
  EXPECT_TRUE(slot.armed);
  EXPECT_EQ(slot.owner, 3U); // the pending expiry still belongs to 3
}

TEST(LegacySlotModel, ReleaseReopensTheSlotToAnyone) {
  LegacySlot slot{};
  ASSERT_TRUE(try_claim(slot, 3));
  release(slot);
  EXPECT_FALSE(slot.armed);
  EXPECT_TRUE(try_claim(slot, 5));
  EXPECT_EQ(slot.owner, 5U);
}

// Claiming an already-free slot is idempotent — expiry may release a
// slot the guest never re-arms.
TEST(LegacySlotModel, RepeatedReleaseIsHarmless) {
  LegacySlot slot{};
  release(slot);
  release(slot);
  EXPECT_FALSE(slot.armed);
  EXPECT_TRUE(try_claim(slot, 0));
}

} // namespace
