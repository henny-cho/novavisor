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
      {ExceptionClass::HVC_AA64, ElrAdvance::kNone},
      {ExceptionClass::SMC_AA64, ElrAdvance::kBeforeDispatch},
      {ExceptionClass::WFx, ElrAdvance::kBeforeDispatch},
      {ExceptionClass::FP_SIMD, ElrAdvance::kNever},
      {ExceptionClass::MSR_MRS, ElrAdvance::kOnClaim},
      {ExceptionClass::DATA_ABORT_LOWER, ElrAdvance::kPerHandler},
      {ExceptionClass::INST_ABORT_LOWER, ElrAdvance::kFault}, // routed guest class, not emulated
  };
  for (const auto& c : kCases) {
    EXPECT_EQ(elr_policy(c.ec), c.want) << "EC 0x" << std::hex << static_cast<unsigned>(c.ec);
  }
}

// Only the two conduits that arrive with ELR at the instruction are
// stepped over by the router before dispatch — HVC must never be, or
// the guest skips the instruction after it.
TEST(ElrPolicy, OnlySmcAndWfxAdvanceBeforeDispatch) {
  EXPECT_EQ(elr_policy(ExceptionClass::SMC_AA64), ElrAdvance::kBeforeDispatch);
  EXPECT_EQ(elr_policy(ExceptionClass::WFx), ElrAdvance::kBeforeDispatch);
  EXPECT_NE(elr_policy(ExceptionClass::HVC_AA64), ElrAdvance::kBeforeDispatch);
  EXPECT_NE(elr_policy(ExceptionClass::FP_SIMD), ElrAdvance::kBeforeDispatch);
  EXPECT_NE(elr_policy(ExceptionClass::MSR_MRS), ElrAdvance::kBeforeDispatch);
  EXPECT_NE(elr_policy(ExceptionClass::DATA_ABORT_LOWER), ElrAdvance::kBeforeDispatch);
}

// Everything the router does not route lands on the guest-fault path,
// including the EL2-origin classes it panics on.
TEST(ElrPolicy, UnroutedClassesFault) {
  EXPECT_EQ(elr_policy(ExceptionClass::UNKNOWN), ElrAdvance::kFault);
  EXPECT_EQ(elr_policy(ExceptionClass::SVC_AA64), ElrAdvance::kFault);
  EXPECT_EQ(elr_policy(ExceptionClass::SVE), ElrAdvance::kFault);
  EXPECT_EQ(elr_policy(ExceptionClass::BRK), ElrAdvance::kFault);
  EXPECT_EQ(elr_policy(ExceptionClass::DATA_ABORT_CURRENT), ElrAdvance::kFault);
  EXPECT_EQ(elr_policy(ExceptionClass::SERROR), ElrAdvance::kFault);
}

// The router's pre-switch step is a compile-time-visible constant per
// class, so the matrix is usable in static_asserts next to each handler.
static_assert(elr_policy(ExceptionClass::HVC_AA64) == ElrAdvance::kNone);
static_assert(elr_policy(ExceptionClass::WFx) == ElrAdvance::kBeforeDispatch);
static_assert(elr_policy(ExceptionClass::FP_SIMD) == ElrAdvance::kNever);
static_assert(elr_policy(ExceptionClass::MSR_MRS) == ElrAdvance::kOnClaim);

} // namespace
