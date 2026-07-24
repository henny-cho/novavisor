#include "nova/abi/dma.hpp"

#include <array>
#include <cstdint>
#include <gtest/gtest.h>
#include <span>

namespace {

using nova::GuestDescriptor;
using nova::dma::AccessResult;
using nova::dma::Assignment;
using nova::dma::DeviceStream;
using nova::dma::FaultAction;
using nova::dma::PhysicalRange;
using nova::dma::PolicyError;

constexpr std::array<GuestDescriptor, 2> kGuests{{
    {.ipa_base = 0x50000000, .ipa_size = 0x00800000, .load_pa = 0x50000000},
    {.ipa_base = 0x50000000, .ipa_size = 0x08000000, .load_pa = 0x50800000},
}};
constexpr std::array<Assignment, 2>      kAssignments{{
    {.device_id = 1, .stream_id = 0x10, .vm = 0},
    {.device_id = 2, .stream_id = 0x20, .vm = 1},
}};
constexpr std::array<DeviceStream, 2>    kDevices{{
    {.device_id = 1, .stream_id = 0x10},
    {.device_id = 2, .stream_id = 0x20},
}};
constexpr std::array<PhysicalRange, 2>   kProtectedPa{{
    {.base = 0x40000000, .size = 0x00100000},
    {.base = 0x60000000, .size = 0x10000000},
}};
constexpr nova::dma::PolicyLimits        kLimits{.sid_bits = 16, .protected_pa = kProtectedPa};

TEST(DmaPolicy, ValidatesUniqueOwnershipAndDisjointBacking) {
  const auto result = nova::dma::validate_policy(kAssignments, kDevices, kGuests, kLimits);
  EXPECT_TRUE(result.ok());
}

TEST(DmaPolicy, RejectsDuplicateStreamAndInvalidOwner) {
  constexpr std::array<Assignment, 2> duplicate{{
      {.device_id = 1, .stream_id = 0x10, .vm = 0},
      {.device_id = 2, .stream_id = 0x10, .vm = 1},
  }};
  auto                                result = nova::dma::validate_policy(duplicate, kGuests, kLimits);
  EXPECT_EQ(result.error, PolicyError::kDuplicateStream);
  EXPECT_EQ(result.index, 1U);
  EXPECT_EQ(result.related_index, 0U);

  constexpr std::array<Assignment, 1> invalid{{{.device_id = 3, .stream_id = 0x30, .vm = 2}}};
  result = nova::dma::validate_policy(invalid, kGuests, kLimits);
  EXPECT_EQ(result.error, PolicyError::kInvalidOwner);
}

TEST(DmaPolicy, RejectsInvalidOrOverlappingGuestWindows) {
  auto invalid        = kGuests;
  invalid[0].ipa_size = 0;
  auto result         = nova::dma::validate_policy(kAssignments, invalid, kLimits);
  EXPECT_EQ(result.error, PolicyError::kInvalidGuestWindow);

  invalid = kGuests;
  invalid[0].load_pa += 1;
  result = nova::dma::validate_policy(kAssignments, invalid, kLimits);
  EXPECT_EQ(result.error, PolicyError::kInvalidGuestWindow);

  auto overlapping       = kGuests;
  overlapping[1].load_pa = 0x50700000;
  result                 = nova::dma::validate_policy(kAssignments, overlapping, kLimits);
  EXPECT_EQ(result.error, PolicyError::kOverlappingGuestPa);
  EXPECT_EQ(result.index, 1U);
  EXPECT_EQ(result.related_index, 0U);
}

TEST(DmaPolicy, HandlesWindowEndingAtMaximumAddress) {
  constexpr std::array<GuestDescriptor, 1> top{{
      {.ipa_base = UINT64_MAX - 0xFFFU, .ipa_size = 0x1000, .load_pa = UINT64_MAX - 0xFFFU},
  }};
  constexpr std::array<Assignment, 1>      assignment{{{.device_id = 1, .stream_id = 0x10, .vm = 0}}};

  constexpr nova::dma::PolicyLimits limits{.sid_bits = 32};
  EXPECT_TRUE(nova::dma::validate_policy(assignment, top, limits).ok());
  const auto decision = nova::dma::decide_access(assignment, top, 0x10, UINT64_MAX - 7U, 8);
  EXPECT_TRUE(decision.allowed());
  EXPECT_EQ(decision.pa, UINT64_MAX - 7U);
}

TEST(DmaPolicy, RejectsUnsupportedStreamIdAndProtectedBacking) {
  constexpr std::array<Assignment, 1> wide_sid{{{.device_id = 1, .stream_id = 0x100, .vm = 0}}};
  constexpr nova::dma::PolicyLimits   narrow{.sid_bits = 8};
  auto                                result = nova::dma::validate_policy(wide_sid, kGuests, narrow);
  EXPECT_EQ(result.error, PolicyError::kStreamIdOutOfRange);

  auto protected_guest       = kGuests;
  protected_guest[0].load_pa = kProtectedPa[1].base;
  result                     = nova::dma::validate_policy(kAssignments, protected_guest, kLimits);
  EXPECT_EQ(result.error, PolicyError::kProtectedPaOverlap);
  EXPECT_EQ(result.index, 0U);
  EXPECT_EQ(result.related_index, 1U);
}

TEST(DmaPolicy, RejectsInvalidHardwareCapabilities) {
  EXPECT_EQ(nova::dma::validate_policy(kAssignments, kGuests, {.sid_bits = 0}).error,
            PolicyError::kInvalidCapabilities);
  EXPECT_EQ(nova::dma::validate_policy(kAssignments, kGuests, {.sid_bits = 33}).error,
            PolicyError::kInvalidCapabilities);
}

TEST(DmaPolicy, ValidatesDeviceStreamsAndSingleOwner) {
  constexpr std::array<DeviceStream, 3> multi_sid_devices{{
      {.device_id = 1, .stream_id = 0x10},
      {.device_id = 1, .stream_id = 0x11},
      {.device_id = 2, .stream_id = 0x20},
  }};
  constexpr std::array<Assignment, 3>   assigned{{
      {.device_id = 1, .stream_id = 0x10, .vm = 0},
      {.device_id = 1, .stream_id = 0x11, .vm = 0},
      {.device_id = 2, .stream_id = 0x20, .vm = 1},
  }};
  EXPECT_TRUE(nova::dma::validate_policy(assigned, multi_sid_devices, kGuests, kLimits).ok());
  EXPECT_EQ(nova::dma::owner_of(assigned, 1), 0U);
  EXPECT_EQ(nova::dma::owner_of(assigned, 2), 1U);
  EXPECT_EQ(nova::dma::owner_of(assigned, 3), nova::dma::kNoVm);

  auto conflicting  = assigned;
  conflicting[1].vm = 1;
  EXPECT_EQ(nova::dma::validate_policy(conflicting, multi_sid_devices, kGuests, kLimits).error,
            PolicyError::kConflictingDeviceOwner);
  EXPECT_EQ(nova::dma::owner_of(conflicting, 1), nova::dma::kNoVm);
}

TEST(DmaPolicy, RejectsUnknownAndPartialDeviceAssignments) {
  constexpr std::array<DeviceStream, 2> device{{
      {.device_id = 1, .stream_id = 0x10},
      {.device_id = 1, .stream_id = 0x11},
  }};
  constexpr std::array<Assignment, 1>   unknown{{
      {.device_id = 2, .stream_id = 0x10, .vm = 0},
  }};
  EXPECT_EQ(nova::dma::validate_policy(unknown, device, kGuests, kLimits).error, PolicyError::kUnknownDeviceStream);

  constexpr std::array<Assignment, 1> partial{{
      {.device_id = 1, .stream_id = 0x10, .vm = 0},
  }};
  EXPECT_EQ(nova::dma::validate_policy(partial, device, kGuests, kLimits).error,
            PolicyError::kIncompleteDeviceAssignment);
}

TEST(DmaPolicy, SameIovaMapsThroughStreamOwner) {
  const auto vm0 = nova::dma::decide_access(kAssignments, kGuests, 0x10, 0x50001000, 0x1000);
  ASSERT_TRUE(vm0.allowed());
  EXPECT_EQ(vm0.vm, 0U);
  EXPECT_EQ(vm0.pa, 0x50001000U);

  const auto vm1 = nova::dma::decide_access(kAssignments, kGuests, 0x20, 0x50001000, 0x1000);
  ASSERT_TRUE(vm1.allowed());
  EXPECT_EQ(vm1.vm, 1U);
  EXPECT_EQ(vm1.pa, 0x50801000U);
}

TEST(DmaPolicy, BlocksUnassignedAndOutOfWindowAccess) {
  const auto unassigned = nova::dma::decide_access(kAssignments, kGuests, 0x99, 0x50000000, 8);
  EXPECT_EQ(unassigned.result, AccessResult::kUnassignedStream);
  EXPECT_EQ(unassigned.action, FaultAction::kBlockAndAudit);

  const auto other_vm = nova::dma::decide_access(kAssignments, kGuests, 0x10, 0x50800000, 8);
  EXPECT_EQ(other_vm.result, AccessResult::kOutsideGuestWindow);
  EXPECT_EQ(other_vm.action, FaultAction::kQuarantineAndResetVm);
  EXPECT_EQ(other_vm.vm, 0U);

  const auto hypervisor = nova::dma::decide_access(kAssignments, kGuests, 0x10, 0x40000000, 8);
  EXPECT_EQ(hypervisor.result, AccessResult::kOutsideGuestWindow);

  const auto pristine = nova::dma::decide_access(kAssignments, kGuests, 0x10, 0x60100000, 8);
  EXPECT_EQ(pristine.result, AccessResult::kOutsideGuestWindow);
}

TEST(DmaPolicy, RejectsEmptyCrossBoundaryAndOverflowingAccess) {
  EXPECT_EQ(nova::dma::decide_access(kAssignments, kGuests, 0x10, 0x50000000, 0).result,
            AccessResult::kOutsideGuestWindow);
  EXPECT_EQ(nova::dma::decide_access(kAssignments, kGuests, 0x10, 0x507FFFF8, 16).result,
            AccessResult::kOutsideGuestWindow);
  EXPECT_EQ(nova::dma::decide_access(kAssignments, kGuests, 0x10, UINT64_MAX - 3U, 8).result,
            AccessResult::kOutsideGuestWindow);
}

TEST(DmaPolicy, TreatsCorruptRuntimePolicyAsHypervisorFailure) {
  constexpr std::array<Assignment, 1> invalid{{{.device_id = 3, .stream_id = 0x30, .vm = 2}}};
  auto                                decision = nova::dma::decide_access(invalid, kGuests, 0x30, 0x50000000, 8);
  EXPECT_EQ(decision.result, AccessResult::kInvalidPolicy);
  EXPECT_EQ(decision.action, FaultAction::kHaltHypervisor);

  constexpr std::array<Assignment, 2> duplicate{{
      {.device_id = 1, .stream_id = 0x10, .vm = 0},
      {.device_id = 2, .stream_id = 0x10, .vm = 1},
  }};
  decision = nova::dma::decide_access(duplicate, kGuests, 0x10, 0x50000000, 8);
  EXPECT_EQ(decision.result, AccessResult::kInvalidPolicy);
}

} // namespace
