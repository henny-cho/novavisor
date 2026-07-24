#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <span>

namespace nova::payload {

inline constexpr std::uint64_t kLoadAlignment = 0x1000;

struct Metadata {
  const std::uint8_t* image;
  const std::uint8_t* dtb_start;
  const std::uint8_t* dtb_end;
  std::uint64_t       load_pa;
  std::uint64_t       entry;
  std::uint64_t       memory_size;
  std::uint64_t       image_size;
  std::uint32_t       checksum;
  std::uint32_t       reserved;
};

static_assert(sizeof(Metadata) == 64);
static_assert(offsetof(Metadata, image) == 0);
static_assert(offsetof(Metadata, dtb_start) == 8);
static_assert(offsetof(Metadata, load_pa) == 24);
static_assert(offsetof(Metadata, checksum) == 56);

[[nodiscard]] consteval auto make_crc32_table() -> std::array<std::uint32_t, 256> {
  std::array<std::uint32_t, 256> table{};
  for (std::uint32_t i = 0; i < table.size(); ++i) {
    std::uint32_t value = i;
    for (std::size_t bit = 0; bit < 8; ++bit) {
      value = (value >> 1U) ^ ((value & 1U) != 0U ? 0xEDB88320U : 0U);
    }
    table[i] = value;
  }
  return table;
}

inline constexpr auto kCrc32Table = make_crc32_table();

[[nodiscard]] constexpr auto checksum32(std::span<const std::uint8_t> bytes) noexcept -> std::uint32_t {
  std::uint32_t crc = 0xFFFFFFFFU;
  for (const std::uint8_t byte : bytes) {
    crc = kCrc32Table[(crc ^ byte) & 0xFFU] ^ (crc >> 8U);
  }
  return crc ^ 0xFFFFFFFFU;
}

struct Layout {
  std::uintptr_t source     = 0;
  std::uint64_t  image_size = 0;
  std::uint64_t  load_pa    = 0;
  std::uint64_t  ipa_base   = 0;
  std::uint64_t  ipa_size   = 0;
  std::uint64_t  entry      = 0;
  std::uint64_t  dtb_ipa    = 0;
  std::uint32_t  checksum   = 0;
};

[[nodiscard]] constexpr auto range_valid(std::uint64_t base, std::uint64_t size) noexcept -> bool {
  return size != 0 && base <= ~std::uint64_t{0} - size;
}

[[nodiscard]] constexpr auto ranges_overlap(std::uint64_t lhs_base, std::uint64_t lhs_size, std::uint64_t rhs_base,
                                            std::uint64_t rhs_size) noexcept -> bool {
  return lhs_base < rhs_base + rhs_size && rhs_base < lhs_base + lhs_size;
}

[[nodiscard]] constexpr auto layout_valid(const Layout& layout) noexcept -> bool {
  if (layout.image_size == 0) {
    return layout.source == 0 && layout.checksum == 0;
  }
  if (layout.source == 0 || layout.load_pa % kLoadAlignment != 0 || !range_valid(layout.source, layout.image_size) ||
      !range_valid(layout.load_pa, layout.image_size) || !range_valid(layout.ipa_base, layout.ipa_size) ||
      layout.image_size > layout.ipa_size || layout.entry < layout.ipa_base ||
      layout.entry >= layout.ipa_base + layout.ipa_size || layout.dtb_ipa < layout.ipa_base ||
      layout.dtb_ipa > layout.ipa_base + layout.ipa_size || layout.image_size > layout.dtb_ipa - layout.ipa_base ||
      ranges_overlap(layout.source, layout.image_size, layout.load_pa, layout.image_size)) {
    return false;
  }
  return true;
}

[[nodiscard]] constexpr auto contents_valid(const Layout& layout, std::span<const std::uint8_t> image) noexcept
    -> bool {
  if (image.size() != layout.image_size) {
    return false;
  }
  return image.empty() || checksum32(image) == layout.checksum;
}

} // namespace nova::payload
