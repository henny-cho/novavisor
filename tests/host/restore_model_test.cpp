#include "hal/restore_model.hpp"

#include <array>
#include <cstdint>
#include <gtest/gtest.h>
#include <span>

namespace {

using nova::memory::restore_changed_words;

TEST(RestoreChangedWords, SkipsUnchangedBlocks) {
  std::array<std::uint64_t, 4> pristine{1, 2, 3, 4};
  auto                         destination = pristine;

  const auto stats = restore_changed_words(std::span{destination}, std::span<const std::uint64_t>{pristine});

  EXPECT_EQ(destination, pristine);
  EXPECT_EQ(stats.examined_bytes, 32U);
  EXPECT_EQ(stats.written_bytes, 0U);
}

TEST(RestoreChangedWords, RestoresOnlyChangedBlocks) {
  const std::array<std::uint64_t, 5> pristine{1, 2, 3, 4, 5};
  std::array<std::uint64_t, 5>       destination{1, 9, 3, 4, 8};

  const auto stats = restore_changed_words(std::span{destination}, std::span<const std::uint64_t>{pristine});

  EXPECT_EQ(destination, pristine);
  EXPECT_EQ(stats.examined_bytes, 40U);
  EXPECT_EQ(stats.written_bytes, 24U);
}

TEST(RestoreChangedWords, RestoresDenseChangesExactly) {
  const std::array<std::uint64_t, 4> pristine{1, 2, 3, 4};
  std::array<std::uint64_t, 4>       destination{};

  const auto stats = restore_changed_words(std::span{destination}, std::span<const std::uint64_t>{pristine});

  EXPECT_EQ(destination, pristine);
  EXPECT_EQ(stats.examined_bytes, 32U);
  EXPECT_EQ(stats.written_bytes, 32U);
}

} // namespace
