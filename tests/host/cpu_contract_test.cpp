#include "nova/arch/cpu_contract.hpp"

#include <gtest/gtest.h>

namespace {

using nova::arch::CpuContractError;
using nova::arch::validate_cpu_contract;

constexpr std::uint64_t kPa40 = 0b010;

// PARange nibble at [3:0], TGran4 at [31:28], TGran4_2 at [43:40].
constexpr auto mmfr0(std::uint64_t pa_range, std::uint64_t tgran4, std::uint64_t tgran4_2) -> std::uint64_t {
  return pa_range | (tgran4 << 28U) | (tgran4_2 << 40U);
}

TEST(CpuContract, AcceptsLargeEnoughPaRange) {
  EXPECT_EQ(validate_cpu_contract(mmfr0(0b010, 0, 0), kPa40), CpuContractError::kNone);
  EXPECT_EQ(validate_cpu_contract(mmfr0(0b101, 0, 0), kPa40), CpuContractError::kNone);
}

TEST(CpuContract, RejectsPaRangeBelowConfiguredPs) {
  EXPECT_EQ(validate_cpu_contract(mmfr0(0b000, 0, 0), kPa40), CpuContractError::kPaRangeTooSmall);
  EXPECT_EQ(validate_cpu_contract(mmfr0(0b001, 0, 0), kPa40), CpuContractError::kPaRangeTooSmall);
}

TEST(CpuContract, Stage2GranuleFollowsTGran4WhenUnspecified) {
  EXPECT_EQ(validate_cpu_contract(mmfr0(0b101, 0x0, 0x0), kPa40), CpuContractError::kNone);
  EXPECT_EQ(validate_cpu_contract(mmfr0(0b101, 0xF, 0x0), kPa40), CpuContractError::kNoStage2Gran4);
}

TEST(CpuContract, Stage2GranuleOverrideWins) {
  EXPECT_EQ(validate_cpu_contract(mmfr0(0b101, 0x0, 0x1), kPa40), CpuContractError::kNoStage2Gran4);
  EXPECT_EQ(validate_cpu_contract(mmfr0(0b101, 0xF, 0x2), kPa40), CpuContractError::kNone);
}

} // namespace
