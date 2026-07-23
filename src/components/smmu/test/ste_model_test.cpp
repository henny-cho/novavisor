#include "smmu/ste_model.hpp"

#include <gtest/gtest.h>

namespace {

using namespace nova::smmu;

TEST(SmmuSte, EncodesNovaStage2Policy) {
  constexpr std::uint64_t root   = 0x0000'0000'1234'5000;
  constexpr std::uint16_t vmid   = 0x1234;
  constexpr auto          result = make_stage2_ste(root, vmid);

  static_assert(result.ok());
  EXPECT_EQ(result.entry[0], 0xD);
  EXPECT_EQ(result.entry[1], 0);
  EXPECT_EQ(result.entry[2], 0x044A'3F59'0000'1234);
  EXPECT_EQ(result.entry[2] & ste::kVmidMask, vmid);
  EXPECT_EQ((result.entry[2] >> ste::kT0szShift) & 0x3F, ste::kT0sz);
  EXPECT_EQ((result.entry[2] >> ste::kSl0Shift) & 0x3, ste::kSl0);
  EXPECT_EQ((result.entry[2] >> ste::kIrgn0Shift) & 0x3, ste::kWriteBack);
  EXPECT_EQ((result.entry[2] >> ste::kOrgn0Shift) & 0x3, ste::kWriteBack);
  EXPECT_EQ((result.entry[2] >> ste::kSh0Shift) & 0x3, ste::kInnerShareable);
  EXPECT_EQ((result.entry[2] >> ste::kTg0Shift) & 0x3, ste::kGranule4k);
  EXPECT_EQ((result.entry[2] >> ste::kPsShift) & 0x7, ste::kPhysicalSize40);
  EXPECT_NE(result.entry[2] & ste::kAa64, 0);
  EXPECT_NE(result.entry[2] & ste::kProtectedTableWalk, 0);
  EXPECT_NE(result.entry[2] & ste::kRecordFault, 0);
  EXPECT_EQ(result.entry[3], root);
  EXPECT_TRUE(is_stage2_only(result.entry));
  EXPECT_FALSE(uses_context_descriptor(result.entry));
}

TEST(SmmuSte, LeavesUnusedWordsZero) {
  constexpr auto result = make_stage2_ste(0x4000, 1);
  ASSERT_TRUE(result.ok());
  for (const std::size_t index : {1U, 4U, 5U, 6U, 7U}) {
    EXPECT_EQ(result.entry[index], 0);
  }
  EXPECT_EQ(sizeof(result.entry), kStreamTableEntryBytes);
}

TEST(SmmuSte, RejectsMisalignedAndUnencodableRoots) {
  EXPECT_EQ(make_stage2_ste(0x1234, 1).error, SteError::kUnalignedRoot);
  EXPECT_EQ(make_stage2_ste(1ULL << 40U, 1).error, SteError::kRootOutOfRange);
}

TEST(SmmuSte, EncodesAbortEntry) {
  constexpr StreamTableEntry entry = make_abort_ste();

  EXPECT_TRUE(is_abort(entry));
  EXPECT_FALSE(is_stage2_only(entry));
  EXPECT_FALSE(uses_context_descriptor(entry));
}

TEST(SmmuSte, DetectsStage1ContextUseSeparately) {
  StreamTableEntry stage1{};
  stage1[0] = ste::kValid | (ste::kStage1Only << ste::kConfigShift);

  EXPECT_TRUE(uses_context_descriptor(stage1));
  EXPECT_FALSE(is_stage2_only(stage1));
}

} // namespace
