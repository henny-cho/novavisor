// hal/arch/aarch64/mem.cpp
//
// Strict-alignment memcpy/memmove/memset overriding the newlib
// versions. EL2 runs with its Stage 1 MMU off, so every access is
// treated as Device memory and must be size-aligned — newlib's
// optimized routines use unaligned wide loads (and DC ZVA in memset,
// which requires Normal cacheable memory) and alignment-fault here.
// Our own code is compiled -mstrict-align; these cover the libcalls
// the compiler emits for aggregate copies and zeroing.
//
// Word-sized chunks when co-aligned, byte loop otherwise. This TU is
// compiled -fno-builtin so the loops cannot be pattern-matched back
// into the very libcalls they implement.

#include <cstddef>
#include <cstdint>

namespace {

inline constexpr std::uintptr_t kWordMask = sizeof(std::uint64_t) - 1;

auto misalign(const void* p) noexcept -> std::uintptr_t {
  return reinterpret_cast<std::uintptr_t>(p) & kWordMask;
}

} // namespace

extern "C" {

auto memcpy(void* dst, const void* src, std::size_t n) noexcept -> void* { // NOLINT(readability-identifier-naming)
  auto*       d = static_cast<unsigned char*>(dst);
  const auto* s = static_cast<const unsigned char*>(src);

  if (misalign(d) == misalign(s)) {
    while (n != 0 && misalign(d) != 0) {
      *d++ = *s++;
      --n;
    }
    while (n >= sizeof(std::uint64_t)) {
      *reinterpret_cast<std::uint64_t*>(d) = *reinterpret_cast<const std::uint64_t*>(s);
      d += sizeof(std::uint64_t);
      s += sizeof(std::uint64_t);
      n -= sizeof(std::uint64_t);
    }
  }
  while (n != 0) {
    *d++ = *s++;
    --n;
  }
  return dst;
}

auto memmove(void* dst, const void* src, std::size_t n) noexcept -> void* { // NOLINT(readability-identifier-naming)
  auto*       d = static_cast<unsigned char*>(dst);
  const auto* s = static_cast<const unsigned char*>(src);
  if (d == s || n == 0) {
    return dst;
  }
  if (d < s || d >= s + n) {
    return memcpy(dst, src, n);
  }
  // Overlapping with dst above src: copy backwards, bytes only (rare
  // path — no aligned fast lane needed).
  d += n;
  s += n;
  while (n != 0) {
    *--d = *--s;
    --n;
  }
  return dst;
}

auto memset(void* dst, int value, std::size_t n) noexcept -> void* { // NOLINT(readability-identifier-naming)
  auto*         d    = static_cast<unsigned char*>(dst);
  const auto    byte = static_cast<unsigned char>(value);
  std::uint64_t word = 0x0101'0101'0101'0101ULL * byte;

  while (n != 0 && misalign(d) != 0) {
    *d++ = byte;
    --n;
  }
  while (n >= sizeof(std::uint64_t)) {
    *reinterpret_cast<std::uint64_t*>(d) = word;
    d += sizeof(std::uint64_t);
    n -= sizeof(std::uint64_t);
  }
  while (n != 0) {
    *d++ = byte;
    --n;
  }
  return dst;
}

} // extern "C"
