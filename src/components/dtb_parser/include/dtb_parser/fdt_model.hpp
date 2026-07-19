#pragma once

// dtb_parser/fdt_model.hpp
//
// Freestanding flattened-device-tree (FDT v17) walker. Pure functions
// over a byte span — no allocation, no exceptions, bounds-checked at
// every read, so a truncated or corrupt blob degrades to "not ok"
// results instead of undefined behaviour. Host-testable.
//
// The hypervisor parses each guest's embedded DTB with parse_guest()
// to build the runtime guest table; the generic node/prop primitives
// stay available for richer trees later.

#include <bit>
#include <cstdint>
#include <span>
#include <string_view>

namespace nova::fdt {

static_assert(std::endian::native == std::endian::little, "byteswap-based reads assume a little-endian host");

inline constexpr std::uint32_t kMagic   = 0xD00DFEEDU;
inline constexpr std::uint32_t kVersion = 17;

// Structure-block tokens.
inline constexpr std::uint32_t kTokBeginNode = 1;
inline constexpr std::uint32_t kTokEndNode   = 2;
inline constexpr std::uint32_t kTokProp      = 3;
inline constexpr std::uint32_t kTokNop       = 4;
inline constexpr std::uint32_t kTokEnd       = 9;

using Bytes = std::span<const std::uint8_t>;

// Big-endian loads. Out-of-bounds reads return 0 — every caller treats
// 0 as "stop / invalid", so a truncated blob cannot walk past the end.
[[nodiscard]] constexpr auto be32(Bytes b, std::size_t off) noexcept -> std::uint32_t {
  if (off + 4 > b.size()) {
    return 0;
  }
  std::uint32_t v = 0;
  for (std::size_t i = 0; i < 4; ++i) {
    v |= static_cast<std::uint32_t>(b[off + i]) << (8U * i);
  }
  return std::byteswap(v);
}

[[nodiscard]] constexpr auto be64(Bytes b, std::size_t off) noexcept -> std::uint64_t {
  return (static_cast<std::uint64_t>(be32(b, off)) << 32U) | be32(b, off + 4);
}

// Validated view: structs/strings sub-spans of a well-formed header.
struct View {
  bool  ok = false;
  Bytes structs{};
  Bytes strings{};
};

[[nodiscard]] constexpr auto make_view(Bytes blob) noexcept -> View {
  constexpr std::size_t kHeaderSize = 40;
  if (blob.size() < kHeaderSize || be32(blob, 0) != kMagic) {
    return {};
  }
  const std::uint32_t total       = be32(blob, 4);
  const std::uint32_t off_struct  = be32(blob, 8);
  const std::uint32_t off_strings = be32(blob, 12);
  const std::uint32_t version     = be32(blob, 20);
  const std::uint32_t last_comp   = be32(blob, 24);
  const std::uint32_t size_str    = be32(blob, 32);
  const std::uint32_t size_struct = be32(blob, 36);
  // 64-bit sums: a hostile header must not wrap the bound checks.
  if (version < kVersion || last_comp > kVersion || total > blob.size() ||
      std::uint64_t{off_struct} + size_struct > total || std::uint64_t{off_strings} + size_str > total) {
    return {};
  }
  return {.ok = true, .structs = blob.subspan(off_struct, size_struct), .strings = blob.subspan(off_strings, size_str)};
}

// NUL-terminated string inside a block; empty view on overrun.
[[nodiscard]] constexpr auto read_string(Bytes block, std::size_t off) noexcept -> std::string_view {
  std::size_t end = off;
  while (end < block.size() && block[end] != 0) {
    ++end;
  }
  if (end >= block.size()) {
    return {};
  }
  return {reinterpret_cast<const char*>(block.data()) + off, end - off};
}

// Offset of the token following the one at `off`, or structs.size()
// (a natural loop terminator) when the token is malformed.
[[nodiscard]] constexpr auto skip_token(Bytes structs, std::size_t off) noexcept -> std::size_t {
  constexpr auto pad4 = [](std::size_t n) { return (n + 3) & ~std::size_t{3}; };
  switch (be32(structs, off)) {
  case kTokBeginNode: {
    const std::string_view name = read_string(structs, off + 4);
    if (name.data() == nullptr) {
      return structs.size();
    }
    return off + 4 + pad4(name.size() + 1);
  }
  case kTokProp: {
    const std::uint32_t len = be32(structs, off + 4);
    return off + 12 + pad4(len);
  }
  case kTokEndNode:
  case kTokNop:
    return off + 4;
  default:
    return structs.size();
  }
}

struct NodeRef {
  bool        ok  = false;
  std::size_t off = 0; // offset of the node's BEGIN_NODE token
};

struct PropRef {
  bool  ok = false;
  Bytes data{};
};

// The root node's BEGIN_NODE is the first structure-block token.
inline constexpr std::size_t kRootNode = 0;

// True when a node named `node_name` answers to `name`: exact match,
// or match up to a unit-address suffix ("memory" ~ "memory@50000000").
[[nodiscard]] constexpr auto name_matches(std::string_view node_name, std::string_view name) noexcept -> bool {
  if (node_name == name) {
    return true;
  }
  return node_name.size() > name.size() && node_name[name.size()] == '@' && node_name.starts_with(name);
}

namespace detail {

// Walk the direct children of `node`, invoking fn(child_off, name) on
// each; fn returning true stops the walk with that child.
template <typename Fn>
[[nodiscard]] constexpr auto for_each_child(const View& v, std::size_t node, Fn&& fn) noexcept -> NodeRef {
  if (be32(v.structs, node) != kTokBeginNode) {
    return {};
  }
  std::size_t off   = skip_token(v.structs, node);
  int         depth = 0;
  while (off < v.structs.size()) {
    const std::uint32_t tok = be32(v.structs, off);
    if (tok == kTokBeginNode) {
      if (depth == 0 && fn(off, read_string(v.structs, off + 4))) {
        return {.ok = true, .off = off};
      }
      ++depth;
    } else if (tok == kTokEndNode) {
      if (depth == 0) {
        return {}; // back at the parent's END_NODE: no (more) match
      }
      --depth;
    } else if (tok == kTokEnd) {
      return {};
    }
    off = skip_token(v.structs, off);
  }
  return {};
}

} // namespace detail

[[nodiscard]] constexpr auto find_child(const View& v, std::size_t node, std::string_view name) noexcept -> NodeRef {
  return detail::for_each_child(v, node, [&](std::size_t, std::string_view n) { return name_matches(n, name); });
}

[[nodiscard]] constexpr auto count_children(const View& v, std::size_t node, std::string_view name) noexcept
    -> std::uint32_t {
  std::uint32_t count = 0;
  (void)detail::for_each_child(v, node, [&](std::size_t, std::string_view n) {
    count += name_matches(n, name) ? 1U : 0U;
    return false;
  });
  return count;
}

// Property of `node` itself (properties precede child nodes).
[[nodiscard]] constexpr auto find_prop(const View& v, std::size_t node, std::string_view name) noexcept -> PropRef {
  if (be32(v.structs, node) != kTokBeginNode) {
    return {};
  }
  std::size_t off = skip_token(v.structs, node);
  while (off < v.structs.size()) {
    const std::uint32_t tok = be32(v.structs, off);
    if (tok == kTokProp) {
      const std::uint32_t len     = be32(v.structs, off + 4);
      const std::uint32_t nameoff = be32(v.structs, off + 8);
      if (off + 12 + len > v.structs.size()) {
        return {};
      }
      if (read_string(v.strings, nameoff) == name) {
        return {.ok = true, .data = v.structs.subspan(off + 12, len)};
      }
    } else if (tok != kTokNop) {
      return {}; // BEGIN_NODE / END_NODE / END: past the property list
    }
    off = skip_token(v.structs, off);
  }
  return {};
}

// Cell accessors; 0 when the index runs off the property.
[[nodiscard]] constexpr auto prop_u32(const PropRef& p, std::size_t index) noexcept -> std::uint32_t {
  return p.ok ? be32(p.data, index * 4) : 0;
}

[[nodiscard]] constexpr auto prop_u64(const PropRef& p, std::size_t index) noexcept -> std::uint64_t {
  return p.ok ? be64(p.data, index * 8) : 0;
}

// ---------------------------------------------------------------------------
// Guest configuration extraction (the yml2dtb schema)
// ---------------------------------------------------------------------------

struct GuestInfo {
  bool          ok       = false;
  std::uint64_t mem_base = 0;
  std::uint64_t mem_size = 0;
  std::uint32_t cpus     = 0;
  bool          has_uart = false;
};

// /memory reg = <base size> (2/2 cells), vcpu count = /cpus children,
// uart presence = a root uart@ node.
[[nodiscard]] constexpr auto parse_guest(Bytes blob) noexcept -> GuestInfo {
  const View v = make_view(blob);
  if (!v.ok) {
    return {};
  }
  const NodeRef mem  = find_child(v, kRootNode, "memory");
  const NodeRef cpus = find_child(v, kRootNode, "cpus");
  if (!mem.ok || !cpus.ok) {
    return {};
  }
  const PropRef reg = find_prop(v, mem.off, "reg");
  if (!reg.ok || reg.data.size() < 16) {
    return {};
  }
  GuestInfo info;
  info.mem_base = prop_u64(reg, 0);
  info.mem_size = prop_u64(reg, 1);
  info.cpus     = count_children(v, cpus.off, "cpu");
  info.has_uart = find_child(v, kRootNode, "uart").ok;
  info.ok       = info.cpus > 0 && info.mem_size > 0;
  return info;
}

} // namespace nova::fdt
