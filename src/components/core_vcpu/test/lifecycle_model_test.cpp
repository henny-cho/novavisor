// Host-side tests for core_vcpu/lifecycle_model.hpp — the micro-reboot
// budget. The invariants under test:
//   - a VM gets exactly kMaxRestarts warm resets per cold start,
//   - exhaustion denies further resets until a refill,
//   - budgets are independent per VM.

#include "core_vcpu/lifecycle_model.hpp"

#include <gtest/gtest.h>

namespace {

using nova::lifecycle::kMaxRestarts;
using Budget = nova::lifecycle::RestartBudget<2>;

TEST(LifecycleModel, GrantsExactlyMaxRestarts) {
  Budget b;
  for (unsigned i = 0; i < kMaxRestarts; ++i) {
    EXPECT_TRUE(b.take(0)) << "restart " << i;
  }
  EXPECT_FALSE(b.take(0));
}

TEST(LifecycleModel, ExhaustionStaysDeniedUntilRefill) {
  Budget b;
  while (b.take(0)) {
  }
  EXPECT_FALSE(b.take(0));
  b.refill(0);
  EXPECT_TRUE(b.take(0));
}

TEST(LifecycleModel, BudgetsAreIndependentPerVm) {
  Budget b;
  while (b.take(0)) {
  }
  EXPECT_FALSE(b.take(0));
  EXPECT_TRUE(b.take(1)); // VM 1 untouched by VM 0's crash loop
}

TEST(LifecycleModel, RefillIsPerVm) {
  Budget b;
  while (b.take(0)) {
  }
  while (b.take(1)) {
  }
  b.refill(0);
  EXPECT_TRUE(b.take(0));
  EXPECT_FALSE(b.take(1));
}

} // namespace
