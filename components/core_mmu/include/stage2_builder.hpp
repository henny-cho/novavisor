#pragma once

// components/core_mmu/include/stage2_builder.hpp
//
// Pure Stage 2 page table population logic — no bare-metal runtime
// dependency, fully host-testable.
//
// Maps arbitrary 4 KiB-aligned IPA→PA ranges (identity or translated by
// a constant offset) within one 1 GiB IPA region: chunks whose IPA slot
// and PA are both 2 MiB-aligned become L2 Block descriptors (no L3
// needed); the rest goes through L3 tables drawn from a caller-provided
// pool. Multiple disjoint ranges may be mapped into the same table set
// (per-VM guest window + shared pages, Phase 8 guest RAM + MMIO).
//
// Reference: ARM ARM DDI0487 §D5.2 (VMSAv8-64 multi-level translation).
//
// Level strides (4 KiB granule):
//   L1  bits 38:30   (entry covers 1 GiB)
//   L2  bits 29:21   (entry covers 2 MiB; Block or Table)
//   L3  bits 20:12   (entry covers 4 KiB, always a Page)

#include "components/core_mmu/include/stage2_descriptor.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <span>

namespace nova::mmu {

inline constexpr std::size_t kTableEntries = 512;

// IPA bit positions where each level's index field starts (4 KiB granule).
inline constexpr std::uint64_t kL1Shift = 30U; // entry covers 1 GiB
inline constexpr std::uint64_t kL2Shift = 21U; // entry covers 2 MiB
inline constexpr std::uint64_t kL3Shift = 12U; // entry covers 4 KiB
// 9-bit index field — one entry per table slot.
inline constexpr std::uint64_t kIndexMask = kTableEntries - 1;

inline constexpr std::uint64_t k1GiB = 1ULL << kL1Shift;
inline constexpr std::uint64_t k2MiB = 1ULL << kL2Shift;
inline constexpr std::uint64_t k4KiB = 1ULL << kL3Shift;

using Table = std::array<std::uint64_t, kTableEntries>;

// Table set for one Stage 2 address space: the L1 root, a single L2
// (all mappings must share one 1 GiB region — QEMU virt RAM
// 0x4000_0000..0x8000_0000 is exactly one), and a pool of L3 tables
// consumed on demand for sub-2 MiB mappings.
//
// The *_pa values are what gets written into Table descriptors; the
// hypervisor passes link-time addresses, host tests pass opaque
// sentinels. l3_pool_pas[i] must be the PA of l3_pool[i], each
// 4 KiB-aligned.
struct Stage2Tables {
  Table*                         l1;
  Table*                         l2;
  std::uint64_t                  l2_pa;
  std::span<Table>               l3_pool;
  std::span<const std::uint64_t> l3_pool_pas;
  std::size_t                    l3_used = 0; // builder-internal cursor
};

// --- Index extractors -------------------------------------------------------

[[nodiscard]] constexpr auto l1_index(std::uint64_t ipa) noexcept -> std::size_t {
  return static_cast<std::size_t>((ipa >> kL1Shift) & kIndexMask);
}

[[nodiscard]] constexpr auto l2_index(std::uint64_t ipa) noexcept -> std::size_t {
  return static_cast<std::size_t>((ipa >> kL2Shift) & kIndexMask);
}

[[nodiscard]] constexpr auto l3_index(std::uint64_t ipa) noexcept -> std::size_t {
  return static_cast<std::size_t>((ipa >> kL3Shift) & kIndexMask);
}

// --- Builder ----------------------------------------------------------------

// Reset every table to kInvalid and rewind the L3 pool.
inline void init_tables(Stage2Tables& t) noexcept {
  t.l1->fill(kInvalid);
  t.l2->fill(kInvalid);
  for (Table& l3 : t.l3_pool) {
    l3.fill(kInvalid);
  }
  t.l3_used = 0;
}

namespace detail {

// Locate the pool table backing an existing L2 Table descriptor.
inline auto find_l3(Stage2Tables& t, std::uint64_t table_pa) noexcept -> Table* {
  for (std::size_t i = 0; i < t.l3_used; ++i) {
    if ((t.l3_pool_pas[i] & desc::kOutputAddrMask) == table_pa) {
      return &t.l3_pool[i];
    }
  }
  return nullptr;
}

} // namespace detail

// Install a mapping of IPA [ipa_base, ipa_base + size) onto PA
// [pa_base, pa_base + size) with the given leaf attributes. Chunks whose
// IPA slot and PA are both 2 MiB-aligned become L2 Blocks; the rest
// becomes L3 Pages from the pool. May be called repeatedly for disjoint
// IPA ranges (call init_tables() once first).
//
// Returns false — with the tables in a partially-written state that must
// not be activated — when:
//   - ipa_base/pa_base/size are not 4 KiB-aligned, or size == 0
//   - the IPA range crosses (or lands outside) the single mapped 1 GiB region
//   - the L3 pool is exhausted
//   - a page range overlaps an already-installed Block
[[nodiscard]] inline auto map_range(Stage2Tables& t, std::uint64_t ipa_base, std::uint64_t pa_base, std::uint64_t size,
                                    std::uint64_t leaf_attrs) noexcept -> bool {
  if (size == 0 || (ipa_base % k4KiB) != 0 || (pa_base % k4KiB) != 0 || (size % k4KiB) != 0) {
    return false;
  }
  // Constant IPA→PA displacement; well-defined under unsigned wraparound
  // even when pa_base < ipa_base.
  const std::uint64_t pa_off = pa_base - ipa_base;

  Table&            l1  = *t.l1;
  Table&            l2  = *t.l2;
  const std::size_t i1  = l1_index(ipa_base);
  const auto        end = ipa_base + size;

  if (l1_index(end - k4KiB) != i1) {
    return false; // crosses a 1 GiB boundary — only one L2 table exists
  }
  // The single L2 serves exactly one L1 slot: reject a second region.
  for (std::size_t i = 0; i < kTableEntries; ++i) {
    if (i != i1 && is_valid(l1[i])) {
      return false;
    }
  }
  if (!is_valid(l1[i1])) {
    l1[i1] = make_table(t.l2_pa);
  }

  std::uint64_t addr = ipa_base;
  while (addr < end) {
    const std::size_t i2 = l2_index(addr);

    // Whole free 2 MiB slot with a Block-alignable PA → Block
    // descriptor, no L3 spent.
    if ((addr % k2MiB) == 0 && ((addr + pa_off) % k2MiB) == 0 && end - addr >= k2MiB && !is_valid(l2[i2])) {
      l2[i2] = make_block(addr + pa_off, leaf_attrs);
      addr += k2MiB;
      continue;
    }

    // Partial slot (or one already backed by pages) → L3 Pages.
    Table* l3 = nullptr;
    if (!is_valid(l2[i2])) {
      if (t.l3_used >= t.l3_pool.size()) {
        return false; // pool exhausted
      }
      l2[i2] = make_table(t.l3_pool_pas[t.l3_used]);
      l3     = &t.l3_pool[t.l3_used];
      ++t.l3_used;
    } else {
      if (descriptor_type(l2[i2]) == desc::kTypeBlock) {
        return false; // overlaps an existing Block mapping
      }
      l3 = detail::find_l3(t, output_addr(l2[i2]));
      if (l3 == nullptr) {
        return false;
      }
    }

    const std::uint64_t slot_end  = (addr & ~(k2MiB - 1)) + k2MiB;
    const std::uint64_t chunk_end = (end < slot_end) ? end : slot_end;
    for (; addr < chunk_end; addr += k4KiB) {
      (*l3)[l3_index(addr)] = make_page(addr + pa_off, leaf_attrs);
    }
  }
  return true;
}

// Convenience: 1:1 identity mapping.
[[nodiscard]] inline auto map_identity_range(Stage2Tables& t, std::uint64_t ipa_base, std::uint64_t size,
                                             std::uint64_t leaf_attrs) noexcept -> bool {
  return map_range(t, ipa_base, ipa_base, size, leaf_attrs);
}

// Convenience: reset + identity-map a single range.
[[nodiscard]] inline auto build_identity_map(Stage2Tables& t, std::uint64_t ipa_base, std::uint64_t size,
                                             std::uint64_t leaf_attrs) noexcept -> bool {
  init_tables(t);
  return map_identity_range(t, ipa_base, size, leaf_attrs);
}

} // namespace nova::mmu
