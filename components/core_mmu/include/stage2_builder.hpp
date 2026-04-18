#pragma once

// components/core_mmu/include/stage2_builder.hpp
//
// Pure Stage 2 page table population logic. Takes L1/L2/L3 tables by
// pointer (plus the physical addresses that will be written into the
// Table descriptors) and installs a 1:1 identity mapping of a single
// contiguous IPA window.
//
// This header has no dependency on the bare-metal runtime — it's
// constexpr-safe where practical and fully host-testable.
//
// Reference: ARM ARM DDI0487 §D5.2 (VMSAv8-64 multi-level translation).
//
// Level strides (4 KiB granule):
//   L1  bits 38:30   (entry covers 1 GiB)
//   L2  bits 29:21   (entry covers 2 MiB)
//   L3  bits 20:12   (entry covers 4 KiB, always a Page)

#include "components/core_mmu/include/stage2_descriptor.hpp"

#include <array>
#include <cstddef>
#include <cstdint>

namespace nova::mmu {

inline constexpr std::size_t   kTableEntries = 512;
inline constexpr std::uint64_t k1GiB         = 1ULL << 30U;
inline constexpr std::uint64_t k2MiB         = 1ULL << 21U;
inline constexpr std::uint64_t k4KiB         = 1ULL << 12U;

using Table = std::array<std::uint64_t, kTableEntries>;

// Bundle of tables and their physical addresses. For Phase 5 the PAs are
// the static array addresses taken at link time and written directly into
// the Table descriptors (L1→L2, L2→L3).
struct Stage2Tables {
  Table*        l1;
  Table*        l2;
  Table*        l3;
  std::uint64_t l2_pa;
  std::uint64_t l3_pa;
};

// --- Index extractors -------------------------------------------------------

[[nodiscard]] constexpr std::size_t l1_index(std::uint64_t ipa) noexcept {
  return static_cast<std::size_t>((ipa >> 30U) & 0x1FFU);
}

[[nodiscard]] constexpr std::size_t l2_index(std::uint64_t ipa) noexcept {
  return static_cast<std::size_t>((ipa >> 21U) & 0x1FFU);
}

[[nodiscard]] constexpr std::size_t l3_index(std::uint64_t ipa) noexcept {
  return static_cast<std::size_t>((ipa >> 12U) & 0x1FFU);
}

// --- Identity-map builder ---------------------------------------------------
//
// Zero all three tables, then install a 1:1 identity mapping covering
// [ipa_base, ipa_base + size). On return:
//   L1[l1_index(ipa_base)]      = Table descriptor → t.l2_pa
//   L2[l2_index(ipa_base)]      = Table descriptor → t.l3_pa
//   L3[l3_index(ipa_base) + i]  = Page descriptor  → (ipa_base + i * 4 KiB)
//                                 for i in [0, size / 4 KiB)
// All other entries are kInvalid.
//
// Constraints (caller must satisfy; violating them corrupts the table):
//   - ipa_base % 4 KiB == 0
//   - size > 0 and size % 4 KiB == 0
//   - ipa_base and (ipa_base + size - 4 KiB) share the same L1 and L2
//     index (i.e., the range lies entirely inside one 2 MiB block)
//   - t.l2_pa and t.l3_pa are 4 KiB-aligned
//
// Phase 5 uses this for a single 1 MiB guest window. Phase 8+ will add
// multi-range and MMIO device variants.
inline void build_identity_map(Stage2Tables& t, std::uint64_t ipa_base, std::uint64_t size,
                               std::uint64_t leaf_attrs) noexcept {
  // Subscript through raw pointers: the bare-metal libstdc++ std::array
  // operator[] / fill() pull in __glibcxx_assert_fail which has no
  // freestanding definition. data() is a trivial getter with no such
  // dependency, so indexing stays identical on host and cross builds.
  auto* l1 = t.l1->data();
  auto* l2 = t.l2->data();
  auto* l3 = t.l3->data();

  for (std::size_t i = 0; i < kTableEntries; ++i) {
    l1[i] = kInvalid;
    l2[i] = kInvalid;
    l3[i] = kInvalid;
  }

  l1[l1_index(ipa_base)] = make_table(t.l2_pa);
  l2[l2_index(ipa_base)] = make_table(t.l3_pa);

  const std::size_t pages    = static_cast<std::size_t>(size / k4KiB);
  const std::size_t i3_start = l3_index(ipa_base);
  for (std::size_t i = 0; i < pages; ++i) {
    const std::uint64_t page_pa = ipa_base + (static_cast<std::uint64_t>(i) * k4KiB);
    l3[i3_start + i]            = make_page(page_pa, leaf_attrs);
  }
}

} // namespace nova::mmu
