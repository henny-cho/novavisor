// tests/host/guest_descriptor_test.cpp
//
// Host-side GTest suite for GuestDescriptor::contains (nova/abi/
// guest.hpp) — the bounds check that stops a guest from pointing an
// HVC_PUTS buffer at hypervisor memory and reading EL2 out through the
// UART. The window arithmetic is unsigned, so the caller's length clamp
// is part of the contract and pinned here too.

#include "nova/abi/guest.hpp"

#include <cstdint>
#include <gtest/gtest.h>

namespace {

using nova::GuestDescriptor;

constexpr std::uint64_t kBase = 0x4000'0000;
constexpr std::uint64_t kSize = 0x0010'0000;

constexpr GuestDescriptor kGuest{.ipa_base = kBase, .ipa_size = kSize};

TEST(GuestDescriptorContains, AcceptsBuffersInsideTheWindow) {
  EXPECT_TRUE(kGuest.contains(kBase, 16));
  EXPECT_TRUE(kGuest.contains(kBase + 0x1000, 64));
  EXPECT_TRUE(kGuest.contains(kBase + kSize / 2, 1));
}

TEST(GuestDescriptorContains, AcceptsTheExactLastByte) {
  EXPECT_TRUE(kGuest.contains(kBase + kSize - 1, 1));
  EXPECT_TRUE(kGuest.contains(kBase + kSize - 16, 16));
}

TEST(GuestDescriptorContains, RejectsBufferStraddlingTheEnd) {
  EXPECT_FALSE(kGuest.contains(kBase + kSize - 8, 16));
  EXPECT_FALSE(kGuest.contains(kBase + kSize, 1));
  EXPECT_FALSE(kGuest.contains(kBase + kSize - 1, 2));
}

TEST(GuestDescriptorContains, RejectsBufferStraddlingTheStart) {
  EXPECT_FALSE(kGuest.contains(kBase - 8, 16));
  EXPECT_FALSE(kGuest.contains(kBase - 1, 1));
  EXPECT_FALSE(kGuest.contains(0, 16)); // the hypervisor's own low memory
}

TEST(GuestDescriptorContains, RejectsAddressesFarOutsideTheWindow) {
  EXPECT_FALSE(kGuest.contains(kBase + 16 * kSize, 1));
  EXPECT_FALSE(kGuest.contains(~std::uint64_t{0}, 1)); // no wrap into the window
}

TEST(GuestDescriptorContains, WholeWindowIsAcceptedOnlyAtTheBase) {
  EXPECT_TRUE(kGuest.contains(kBase, kSize));
  EXPECT_FALSE(kGuest.contains(kBase + 1, kSize));
}

// A zero-length buffer is in bounds anywhere from the base to one past
// the end: nothing is dereferenced, so the check has nothing to reject.
TEST(GuestDescriptorContains, ZeroLengthIsInBoundsUpToOnePastTheEnd) {
  EXPECT_TRUE(kGuest.contains(kBase, 0));
  EXPECT_TRUE(kGuest.contains(kBase + kSize, 0));
  EXPECT_FALSE(kGuest.contains(kBase - 1, 0));
}

// A length merely longer than the window is still rejected everywhere:
// the end-of-window bound drops below ipa_base, which no ipa satisfies.
TEST(GuestDescriptorContains, LengthPastTheWindowRejectsEveryAddress) {
  EXPECT_FALSE(kGuest.contains(kBase, kSize + 1));
  EXPECT_FALSE(kGuest.contains(kBase + 0x1000, kSize + 1));
}

// Why callers must clamp len BEFORE asking: `ipa_base + ipa_size - len`
// is unsigned, so a length past the window's END ADDRESS underflows to a
// near-max bound and the check degenerates into "ipa >= ipa_base" — it
// accepts a buffer running arbitrarily far past the window into
// hypervisor memory. demo_hvc's handle_puts clamps to kMaxPutsLen
// (<= any window size) first, which is what keeps this unreachable.
TEST(GuestDescriptorContains, OverlongLengthUnderflowsSoCallersClampFirst) {
  constexpr std::uint64_t kPastEndAddress = kBase + kSize + 1;
  EXPECT_TRUE(kGuest.contains(kBase, kPastEndAddress)); // NOT a safety verdict — underflowed

  // The clamp the real caller applies restores the correct verdict at
  // the same boundary.
  constexpr std::uint64_t kMaxPutsLen = 256;
  constexpr std::uint64_t clamped     = (kPastEndAddress > kMaxPutsLen) ? kMaxPutsLen : kPastEndAddress;
  EXPECT_TRUE(kGuest.contains(kBase, clamped));
  EXPECT_TRUE(kGuest.contains(kBase + kSize - clamped, clamped));
  EXPECT_FALSE(kGuest.contains(kBase + kSize - clamped + 1, clamped));
}

} // namespace
