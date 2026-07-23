#include "smmu/command_model.hpp"

#include <gtest/gtest.h>

namespace {

using namespace nova::smmu;

TEST(SmmuCommand, EncodesConfigurationInvalidation) {
  constexpr CommandEntry command = make_cfgi_ste(0x1234);

  EXPECT_EQ(command, (CommandEntry{0x0000'1234'0000'0003, 1}));
  EXPECT_EQ(command_opcode(command), 0x03);
}

TEST(SmmuCommand, EncodesStage2TlbInvalidation) {
  constexpr CommandEntry command = make_tlbi_s12_vmall(0x4567);

  EXPECT_EQ(command, (CommandEntry{0x0000'4567'0000'0028, 0}));
  EXPECT_EQ(command_opcode(command), 0x28);
}

TEST(SmmuCommand, EncodesGlobalTlbInvalidationAndSync) {
  EXPECT_EQ(make_tlbi_nsnh_all(), (CommandEntry{0x30, 0}));
  EXPECT_EQ(make_command_sync(), (CommandEntry{0x0FC0'0046, 0}));
}

} // namespace
