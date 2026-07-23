#include "smmu/domain_model.hpp"

#include <array>
#include <gtest/gtest.h>

namespace {

using namespace nova;
using namespace nova::smmu;

constexpr std::array<GuestDescriptor, 2> kGuests{{
    {.ipa_base = 0x5000'0000, .ipa_size = 0x20'0000, .load_pa = 0x5000'0000, .vmid = 1},
    {.ipa_base = 0x5000'0000, .ipa_size = 0x20'0000, .load_pa = 0x5020'0000, .vmid = 2},
}};

constexpr std::array<TranslationContext, 2> kContexts{{
    {.owner_vm = 0, .vmid = 1, .root_pa = 0x4000},
    {.owner_vm = 1, .vmid = 2, .root_pa = 0x8000},
}};

TEST(SmmuDomain, AcceptsDedicatedGuestContexts) {
  EXPECT_EQ(validate_contexts(kContexts, kGuests, false), ContextError::kNone);
}

TEST(SmmuDomain, RejectsInvalidOwnershipAndIdentity) {
  auto contexts        = kContexts;
  contexts[1].owner_vm = 0;
  EXPECT_EQ(validate_contexts(contexts, kGuests, false), ContextError::kInvalidOwner);

  contexts         = kContexts;
  contexts[1].vmid = 1;
  EXPECT_EQ(validate_contexts(contexts, kGuests, false), ContextError::kInvalidVmid);

  auto guests      = kGuests;
  guests[1].vmid   = 1;
  contexts         = kContexts;
  contexts[1].vmid = 1;
  EXPECT_EQ(validate_contexts(contexts, guests, false), ContextError::kDuplicateVmid);
}

TEST(SmmuDomain, RejectsInvalidOrSharedRoots) {
  auto contexts = kContexts;
  contexts[1].root_pa += 8;
  EXPECT_EQ(validate_contexts(contexts, kGuests, false), ContextError::kUnalignedRoot);

  contexts            = kContexts;
  contexts[1].root_pa = 1ULL << 40U;
  EXPECT_EQ(validate_contexts(contexts, kGuests, false), ContextError::kRootOutOfRange);

  auto guests       = kGuests;
  guests[1].load_pa = (1ULL << 40U) - 0x10'0000;
  EXPECT_EQ(validate_contexts(kContexts, guests, false), ContextError::kGuestPaOutOfRange);

  contexts            = kContexts;
  contexts[1].root_pa = contexts[0].root_pa;
  EXPECT_EQ(validate_contexts(contexts, kGuests, false), ContextError::kDuplicateRoot);
}

TEST(SmmuDomain, EnforcesEightBitVmidCapability) {
  auto guests      = kGuests;
  guests[1].vmid   = 0x100;
  auto contexts    = kContexts;
  contexts[1].vmid = 0x100;

  EXPECT_EQ(validate_contexts(contexts, guests, false), ContextError::kInvalidVmid);
  EXPECT_EQ(validate_contexts(contexts, guests, true), ContextError::kNone);
}

TEST(SmmuDomain, TracksAttachDetachAndQuarantine) {
  StreamBinding binding{};
  EXPECT_TRUE(configure_binding(binding, 1, kGuests.size()));
  EXPECT_EQ(binding.owner_vm, 1);
  EXPECT_EQ(binding.state, DomainState::kDetached);

  EXPECT_TRUE(mark_attached(binding, 1));
  EXPECT_FALSE(mark_attached(binding, 1));
  EXPECT_TRUE(mark_detached(binding));
  EXPECT_TRUE(mark_quarantined(binding));
  EXPECT_FALSE(mark_attached(binding, 1));
  EXPECT_TRUE(mark_attached(binding, 2));
  EXPECT_EQ(binding.generation, 2);
}

TEST(SmmuDomain, RejectsInvalidOrConflictingBinding) {
  StreamBinding binding{};
  EXPECT_FALSE(configure_binding(binding, kGuests.size(), kGuests.size()));
  EXPECT_TRUE(configure_binding(binding, 0, kGuests.size()));
  EXPECT_FALSE(configure_binding(binding, 0, kGuests.size()));
  EXPECT_FALSE(configure_binding(binding, 1, kGuests.size()));
}

} // namespace
