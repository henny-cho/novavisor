#include "dma_device/lifecycle_model.hpp"

#include <gtest/gtest.h>

namespace {

using nova::dma_device::Registry;
using nova::dma_device::State;

TEST(DmaDeviceRegistry, GroupsMultipleStreamsByDevice) {
  Registry<4> registry;
  EXPECT_TRUE(registry.add(1, 0));
  EXPECT_TRUE(registry.add(1, 0));
  EXPECT_TRUE(registry.add(2, 1));
  ASSERT_EQ(registry.entries().size(), 2U);
  EXPECT_EQ(registry.find(1)->owner_vm, 0U);
  EXPECT_EQ(registry.find(2)->owner_vm, 1U);
}

TEST(DmaDeviceRegistry, RejectsConflictingOwnersAndCapacityOverflow) {
  Registry<1> registry;
  EXPECT_TRUE(registry.add(1, 0));
  EXPECT_FALSE(registry.add(1, 1));
  EXPECT_FALSE(registry.add(2, 0));
  EXPECT_FALSE(registry.add(nova::dma::kNoDevice, 0));
}

TEST(DmaDeviceRegistry, RequiresEveryOwnedDeviceAtTheCurrentGeneration) {
  Registry<3> registry;
  ASSERT_TRUE(registry.add(1, 0));
  ASSERT_TRUE(registry.add(2, 0));
  ASSERT_TRUE(registry.add(3, 1));

  registry.find(1)->state      = State::kActive;
  registry.find(1)->generation = 4;
  registry.find(2)->state      = State::kActive;
  registry.find(2)->generation = 4;
  EXPECT_TRUE(registry.owner_active(0, 4));

  registry.find(2)->generation = 3;
  EXPECT_FALSE(registry.owner_active(0, 4));
  EXPECT_FALSE(registry.owner_active(2, 4));

  registry.find(3)->state = State::kFailed;
  EXPECT_TRUE(registry.owner_failed(1));
  EXPECT_FALSE(registry.owner_failed(0));
}

} // namespace
