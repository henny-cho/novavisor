// Host-side tests for core_vcpu/fp_model.hpp — lazy FP/SIMD ownership
// transitions. The invariants under test:
//   - the register file has at most one owner,
//   - a claim names exactly which state must be saved,
//   - only the owner runs untrapped,
//   - invalidation prevents a dead guest's state from being saved.

#include "core_vcpu/fp_model.hpp"

#include <gtest/gtest.h>

namespace {

using nova::fp::kNoOwner;
using nova::fp::Ownership;

TEST(FpModel, StartsUnownedAndTrapping) {
  const Ownership fp;
  EXPECT_EQ(fp.owner(), kNoOwner);
  EXPECT_TRUE(fp.trap_needed(0));
  EXPECT_TRUE(fp.trap_needed(1));
}

TEST(FpModel, FirstClaimHasNothingToSave) {
  Ownership fp;
  EXPECT_EQ(fp.claim(0), kNoOwner);
  EXPECT_EQ(fp.owner(), 0U);
  EXPECT_FALSE(fp.trap_needed(0));
  EXPECT_TRUE(fp.trap_needed(1));
}

TEST(FpModel, SecondClaimSavesThePreviousOwner) {
  Ownership fp;
  fp.claim(0);
  EXPECT_EQ(fp.claim(1), 0U);
  EXPECT_EQ(fp.owner(), 1U);
  EXPECT_TRUE(fp.trap_needed(0));
  EXPECT_FALSE(fp.trap_needed(1));
}

TEST(FpModel, SpuriousClaimByOwnerIsIdentity) {
  Ownership fp;
  fp.claim(2);
  EXPECT_EQ(fp.claim(2), 2U); // caller detects prev == current: no move
  EXPECT_EQ(fp.owner(), 2U);
}

TEST(FpModel, OwnershipBouncesAcrossPreemption) {
  Ownership fp;
  fp.claim(0);
  EXPECT_EQ(fp.claim(1), 0U);
  EXPECT_EQ(fp.claim(0), 1U);
  EXPECT_EQ(fp.claim(1), 0U);
  EXPECT_EQ(fp.owner(), 1U);
}

TEST(FpModel, InvalidateDropsOnlyTheOwner) {
  Ownership fp;
  fp.claim(1);
  fp.invalidate(0); // non-owner: no-op
  EXPECT_EQ(fp.owner(), 1U);
  fp.invalidate(1); // owner reseeded: live state is garbage
  EXPECT_EQ(fp.owner(), kNoOwner);
  // Next claimant must not save the dead state.
  EXPECT_EQ(fp.claim(0), kNoOwner);
}

} // namespace
