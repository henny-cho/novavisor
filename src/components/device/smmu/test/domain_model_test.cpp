#include "smmu/domain_model.hpp"

#include <array>
#include <cstddef>
#include <gtest/gtest.h>
#include <utility>

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

  // An empty window fits inside every address space, so containment
  // alone would admit it — the range has to be a range first.
  guests             = kGuests;
  guests[1].ipa_size = 0;
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
  EXPECT_TRUE(attachment_matches(binding, 1));
  EXPECT_EQ(snapshot_fault(binding, 0x10).owner_vm, 1);
  EXPECT_EQ(snapshot_fault(binding, 0x10).stream_id, 0x10);
  EXPECT_EQ(snapshot_fault(binding, 0x10).generation, 1);
  EXPECT_FALSE(attachment_matches(binding, 2));
  EXPECT_FALSE(mark_attached(binding, 1));
  EXPECT_TRUE(mark_detached(binding));
  EXPECT_FALSE(snapshot_fault(binding, 0x10).valid());
  EXPECT_TRUE(mark_quarantined(binding));
  EXPECT_FALSE(mark_attached(binding, 1));
  EXPECT_TRUE(mark_attached(binding, 2));
  EXPECT_EQ(binding.generation, 2);
  EXPECT_EQ(snapshot_fault(binding, 0x10).generation, 2);
}

TEST(SmmuDomain, CoalescesRepeatedStreamFaultsBeforeRecovery) {
  StreamBinding binding{};
  ASSERT_TRUE(configure_binding(binding, 0, kGuests.size()));
  ASSERT_TRUE(mark_attached(binding, 2));
  constexpr std::array<std::uint32_t, 2> streams{0x10, 0x10};

  const auto notices = collect_fault_notices<streams.size()>(streams, [&](std::uint32_t stream_id) {
    const FaultNotice notice = snapshot_fault(binding, stream_id);
    if (notice.valid()) {
      EXPECT_TRUE(mark_quarantined(binding));
    }
    return notice;
  });

  ASSERT_EQ(notices.count, 1);
  EXPECT_EQ(notices.notices[0].owner_vm, 0);
  EXPECT_EQ(notices.notices[0].stream_id, 0x10);
  EXPECT_EQ(notices.notices[0].generation, 2);
  EXPECT_EQ(binding.state, DomainState::kQuarantined);
}

TEST(SmmuDomain, RejectsInvalidOrConflictingBinding) {
  StreamBinding binding{};
  EXPECT_FALSE(configure_binding(binding, kGuests.size(), kGuests.size()));
  EXPECT_TRUE(configure_binding(binding, 0, kGuests.size()));
  EXPECT_FALSE(configure_binding(binding, 0, kGuests.size()));
  EXPECT_FALSE(configure_binding(binding, 1, kGuests.size()));
}

// --- Per-VM stream index ----------------------------------------------------

constexpr std::size_t kTestStreams = 8;

// Bindings owned as configured by the policy: stream ids are sparse and
// interleaved between the two VMs, the rest stay unconfigured.
[[nodiscard]] auto make_bindings() -> std::array<StreamBinding, kTestStreams> {
  std::array<StreamBinding, kTestStreams> bindings{};
  for (const auto& [sid, vm] : std::array<std::pair<std::size_t, std::size_t>, 4>{{{1, 1}, {2, 0}, {5, 1}, {7, 0}}}) {
    EXPECT_TRUE(configure_binding(bindings[sid], vm, kMaxGuests));
  }
  return bindings;
}

TEST(SmmuStreamIndex, GroupsSparseStreamsByOwner) {
  const auto bindings = make_bindings();
  const auto index    = build_stream_index<kTestStreams>(bindings);

  const auto vm0 = index.streams_of(0);
  ASSERT_EQ(vm0.size(), 2);
  EXPECT_EQ(vm0[0], 2);
  EXPECT_EQ(vm0[1], 7);

  const auto vm1 = index.streams_of(1);
  ASSERT_EQ(vm1.size(), 2);
  EXPECT_EQ(vm1[0], 1);
  EXPECT_EQ(vm1[1], 5);

  // Unconfigured streams belong to nobody.
  EXPECT_TRUE(index.streams_of(2).empty());
  EXPECT_TRUE(index.streams_of(kMaxGuests - 1).empty());
}

TEST(SmmuStreamIndex, IgnoresUnownedAndOutOfRangeEntries) {
  std::array<StreamBinding, kTestStreams> bindings{};
  bindings[0].owner_vm = kMaxGuests;     // owner past the guest table
  bindings[1].owner_vm = kMaxGuests + 3; // ditto
  ASSERT_TRUE(configure_binding(bindings[3], 0, kMaxGuests));

  const auto index = build_stream_index<kTestStreams>(bindings);
  ASSERT_EQ(index.streams_of(0).size(), 1);
  EXPECT_EQ(index.streams_of(0)[0], 3);
  for (std::size_t vm = 1; vm < kMaxGuests; ++vm) {
    EXPECT_TRUE(index.streams_of(vm).empty());
  }
}

TEST(SmmuStreamIndex, OutOfRangeVmHasNoStreams) {
  const auto index = build_stream_index<kTestStreams>(make_bindings());
  EXPECT_TRUE(index.streams_of(kMaxGuests).empty());
  EXPECT_TRUE(index.streams_of(dma::kNoVm).empty());
}

TEST(SmmuAttachGate, AcceptsFreshDomain) {
  const auto bindings = make_bindings();
  const auto index    = build_stream_index<kTestStreams>(bindings);
  EXPECT_TRUE(can_attach_vm(bindings, index.streams_of(0), 1));
  EXPECT_TRUE(can_attach_vm(bindings, index.streams_of(1), 1));
}

TEST(SmmuAttachGate, EmptyStreamListIsTriviallyAttachable) {
  const auto bindings = make_bindings();
  const auto index    = build_stream_index<kTestStreams>(bindings);
  EXPECT_TRUE(can_attach_vm(bindings, index.streams_of(2), 1));
}

TEST(SmmuAttachGate, AllOrNothingAcrossTheDomain) {
  auto       bindings = make_bindings();
  const auto index    = build_stream_index<kTestStreams>(bindings);

  // A single quarantined stream blocks the whole domain at the old
  // generation, and only a newer one lets it back in.
  ASSERT_TRUE(mark_attached(bindings[2], 1));
  ASSERT_TRUE(mark_quarantined(bindings[2]));
  EXPECT_FALSE(can_attach_vm(bindings, index.streams_of(0), 1));
  EXPECT_TRUE(can_attach_vm(bindings, index.streams_of(0), 2));
  EXPECT_TRUE(can_attach_vm(bindings, index.streams_of(1), 1)); // the other VM is untouched
}

TEST(SmmuAttachGate, GenerationMustAdvancePastTheBinding) {
  auto       bindings = make_bindings();
  const auto index    = build_stream_index<kTestStreams>(bindings);
  ASSERT_TRUE(mark_attached(bindings[7], 3));
  ASSERT_TRUE(mark_detached(bindings[7]));

  EXPECT_FALSE(can_attach_vm(bindings, index.streams_of(0), 3)); // replay of the retired generation
  EXPECT_TRUE(can_attach_vm(bindings, index.streams_of(0), 4));
}

TEST(SmmuAttachGate, AttachedDomainIsIdempotentOnlyAtItsGeneration) {
  auto       bindings = make_bindings();
  const auto index    = build_stream_index<kTestStreams>(bindings);
  ASSERT_TRUE(mark_attached(bindings[2], 5));
  ASSERT_TRUE(mark_attached(bindings[7], 5));

  EXPECT_TRUE(can_attach_vm(bindings, index.streams_of(0), 5)); // retry of the same attach
  EXPECT_FALSE(can_attach_vm(bindings, index.streams_of(0), 6));
  EXPECT_FALSE(can_attach_vm(bindings, index.streams_of(0), 4));
}

TEST(SmmuAttachGate, PartiallyAttachedDomainNeedsTheSameGeneration) {
  auto       bindings = make_bindings();
  const auto index    = build_stream_index<kTestStreams>(bindings);
  ASSERT_TRUE(mark_attached(bindings[2], 5)); // one stream installed, one still detached

  EXPECT_TRUE(can_attach_vm(bindings, index.streams_of(0), 5)); // resumes the interrupted attach
  EXPECT_FALSE(can_attach_vm(bindings, index.streams_of(0), 6));
}

} // namespace
