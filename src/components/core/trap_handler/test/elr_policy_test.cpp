// Host-side GTest suite for the ELR_EL2 advance policy
// (components/trap_handler/include/trap_handler/elr_policy.hpp). The
// table pins the whole matrix: a router change that moves a class to a
// different resume rule has to move the expectation with it.

#include "trap_handler/elr_policy.hpp"

#include <gtest/gtest.h>

namespace {

using nova::esr::ExceptionClass;
using nova::trap::elr_policy;
using nova::trap::ElrAdvance;

struct Case {
  ExceptionClass ec;
  ElrAdvance     want;
};

TEST(ElrPolicy, MatrixPerRoutedClass) {
  constexpr Case kCases[] = {
      {ExceptionClass::kHvcAa64, ElrAdvance::kNone},         {ExceptionClass::kSmcAa64, ElrAdvance::kBeforeDispatch},
      {ExceptionClass::kWfx, ElrAdvance::kBeforeDispatch},   {ExceptionClass::kFpSimd, ElrAdvance::kNever},
      {ExceptionClass::kMsrMrs, ElrAdvance::kOnClaim},       {ExceptionClass::kDataAbortLower, ElrAdvance::kPerHandler},
      {ExceptionClass::kInstAbortLower, ElrAdvance::kFault}, // routed guest class, not emulated
  };
  for (const auto& c : kCases) {
    EXPECT_EQ(elr_policy(c.ec), c.want) << "EC 0x" << std::hex << static_cast<unsigned>(c.ec);
  }
}

// Only the two conduits that arrive with ELR at the instruction are
// stepped over by the router before dispatch — HVC must never be, or
// the guest skips the instruction after it.
TEST(ElrPolicy, OnlySmcAndWfxAdvanceBeforeDispatch) {
  EXPECT_EQ(elr_policy(ExceptionClass::kSmcAa64), ElrAdvance::kBeforeDispatch);
  EXPECT_EQ(elr_policy(ExceptionClass::kWfx), ElrAdvance::kBeforeDispatch);
  EXPECT_NE(elr_policy(ExceptionClass::kHvcAa64), ElrAdvance::kBeforeDispatch);
  EXPECT_NE(elr_policy(ExceptionClass::kFpSimd), ElrAdvance::kBeforeDispatch);
  EXPECT_NE(elr_policy(ExceptionClass::kMsrMrs), ElrAdvance::kBeforeDispatch);
  EXPECT_NE(elr_policy(ExceptionClass::kDataAbortLower), ElrAdvance::kBeforeDispatch);
}

// Everything the router does not route lands on the guest-fault path,
// including the EL2-origin classes it panics on.
TEST(ElrPolicy, UnroutedClassesFault) {
  EXPECT_EQ(elr_policy(ExceptionClass::kUnknown), ElrAdvance::kFault);
  EXPECT_EQ(elr_policy(ExceptionClass::kSvcAa64), ElrAdvance::kFault);
  EXPECT_EQ(elr_policy(ExceptionClass::kSve), ElrAdvance::kFault);
  EXPECT_EQ(elr_policy(ExceptionClass::kBrk), ElrAdvance::kFault);
  EXPECT_EQ(elr_policy(ExceptionClass::kDataAbortCurrent), ElrAdvance::kFault);
  EXPECT_EQ(elr_policy(ExceptionClass::kSerror), ElrAdvance::kFault);
}

// The router's pre-switch step is a compile-time-visible constant per
// class, so the matrix is usable in static_asserts next to each handler.
static_assert(elr_policy(ExceptionClass::kHvcAa64) == ElrAdvance::kNone);
static_assert(elr_policy(ExceptionClass::kWfx) == ElrAdvance::kBeforeDispatch);
static_assert(elr_policy(ExceptionClass::kFpSimd) == ElrAdvance::kNever);
static_assert(elr_policy(ExceptionClass::kMsrMrs) == ElrAdvance::kOnClaim);

} // namespace
