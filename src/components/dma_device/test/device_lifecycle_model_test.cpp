#include "dma_device/backend_model.hpp"
#include "dma_device/lifecycle_model.hpp"

#include <array>
#include <gtest/gtest.h>

namespace {

using nova::dma_device::Registry;
using nova::dma_device::State;

bool ok() noexcept {
  return true;
}

bool start(std::uint64_t, std::uint64_t, std::uint64_t, bool) noexcept {
  return true;
}

void clear() noexcept {
}

constexpr auto backend(nova::dma::DeviceId device_id, nova::dma::ResetCapability reset) noexcept
    -> nova::dma_device::Backend {
  return {
      .device_id        = device_id,
      .reset_capability = reset,
      .present          = ok,
      .configure        = ok,
      .quiesce          = ok,
      .drained          = ok,
      .reset            = reset == nova::dma::ResetCapability::kFunction ? ok : nullptr,
      .resume           = ok,
      .start_dma        = start,
      .clear_interrupts = clear,
  };
}

TEST(DmaDeviceRegistry, GroupsMultipleStreamsByDevice) {
  constexpr std::array assignments{
      nova::dma::Assignment{.device_id = 1, .stream_id = 0x10, .vm = 0},
      nova::dma::Assignment{.device_id = 1, .stream_id = 0x11, .vm = 0},
      nova::dma::Assignment{.device_id = 2, .stream_id = 0x20, .vm = 1},
  };
  Registry<4> registry;
  EXPECT_TRUE(registry.load(assignments));
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

  registry.fail_owner(0);
  EXPECT_TRUE(registry.owner_failed(0));
  EXPECT_FALSE(registry.owner_active(0, 4));
  EXPECT_EQ(registry.find(1)->generation, 0U);
  EXPECT_EQ(registry.find(2)->state, State::kFailed);
}

TEST(DmaDeviceBackend, ValidatesAssignedCapabilitiesAndOperations) {
  constexpr std::array assignments{
      nova::dma::Assignment{.device_id = 1, .stream_id = 0x10, .vm = 0},
      nova::dma::Assignment{.device_id = 1, .stream_id = 0x11, .vm = 0},
      nova::dma::Assignment{.device_id = 2, .stream_id = 0x20, .vm = 1},
  };
  constexpr std::array capabilities{
      nova::dma::DeviceCapability{.device_id = 1, .reset = nova::dma::ResetCapability::kQuiesce, .coherent = true},
      nova::dma::DeviceCapability{.device_id = 2, .reset = nova::dma::ResetCapability::kFunction, .coherent = false},
  };
  constexpr std::array backends{
      backend(1, nova::dma::ResetCapability::kQuiesce),
      backend(2, nova::dma::ResetCapability::kFunction),
  };
  EXPECT_TRUE(nova::dma_device::validate_backend_policy(assignments, capabilities, backends, 8).ok());
}

TEST(DmaDeviceBackend, RejectsCapacityMissingBackendAndResetMismatch) {
  constexpr std::array assignments{
      nova::dma::Assignment{.device_id = 1, .stream_id = 0x10, .vm = 0},
  };
  constexpr std::array capabilities{
      nova::dma::DeviceCapability{.device_id = 1, .reset = nova::dma::ResetCapability::kFunction, .coherent = true},
  };
  constexpr std::array no_backends{backend(2, nova::dma::ResetCapability::kQuiesce)};
  EXPECT_EQ(nova::dma_device::validate_backend_policy(assignments, capabilities, no_backends, 8).error,
            nova::dma_device::BackendPolicyError::kMissingBackend);

  constexpr std::array mismatch{backend(1, nova::dma::ResetCapability::kQuiesce)};
  EXPECT_EQ(nova::dma_device::validate_backend_policy(assignments, capabilities, mismatch, 8).error,
            nova::dma_device::BackendPolicyError::kResetMismatch);
  EXPECT_EQ(nova::dma_device::validate_backend_policy(assignments, capabilities, mismatch, 0).error,
            nova::dma_device::BackendPolicyError::kCapacity);

  auto incomplete  = backend(1, nova::dma::ResetCapability::kFunction);
  incomplete.reset = nullptr;
  EXPECT_EQ(nova::dma_device::validate_backend_policy(assignments, capabilities, std::span{&incomplete, 1}, 8).error,
            nova::dma_device::BackendPolicyError::kInvalidBackend);
}

} // namespace
