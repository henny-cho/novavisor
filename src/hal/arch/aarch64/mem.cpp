// hal/arch/aarch64/mem.cpp
//
// Strict-alignment memcpy/memmove/memset/strlen overriding the newlib
// versions. EL2 runs with its Stage 1 MMU off, so every access is
// treated as Device memory and must be size-aligned — newlib's
// optimized routines use unaligned wide loads (and DC ZVA in memset,
// which requires Normal cacheable memory) and alignment-fault here.
// newlib's strlen additionally uses SIMD registers, which EL2 must
// never touch (guest FP state is switched lazily). Our own code is
// compiled -mstrict-align -mgeneral-regs-only; these cover the
// libcalls the compiler emits for aggregate copies and zeroing.
//
// Word-sized chunks when co-aligned, byte loop otherwise. This TU is
// compiled -fno-builtin so the loops cannot be pattern-matched back
// into the very libcalls they implement.

#include "hal/mem.hpp"

#include <cstddef>
#include <cstdint>

namespace {

inline constexpr std::uintptr_t kWordMask = sizeof(std::uint64_t) - 1;
inline constexpr std::uintptr_t kPairMask = (2 * sizeof(std::uint64_t)) - 1;

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
    auto blocks = n / 64U;
    if (blocks != 0 && ((reinterpret_cast<std::uintptr_t>(d) | reinterpret_cast<std::uintptr_t>(s)) & kPairMask) == 0) {
      // Pair and unroll aligned accesses so large pristine-image restores
      // remain practical under TCG without touching guest SIMD state.
      asm volatile("1:\n"
                   "ldp x3, x4, [%[source], #0]\n"
                   "ldp x5, x6, [%[source], #16]\n"
                   "ldp x7, x8, [%[source], #32]\n"
                   "ldp x9, x10, [%[source], #48]\n"
                   "stp x3, x4, [%[destination], #0]\n"
                   "stp x5, x6, [%[destination], #16]\n"
                   "stp x7, x8, [%[destination], #32]\n"
                   "stp x9, x10, [%[destination], #48]\n"
                   "add %[source], %[source], #64\n"
                   "add %[destination], %[destination], #64\n"
                   "subs %[blocks], %[blocks], #1\n"
                   "b.ne 1b\n"
                   : [destination] "+r"(d), [source] "+r"(s), [blocks] "+r"(blocks)
                   :
                   : "x3", "x4", "x5", "x6", "x7", "x8", "x9", "x10", "cc", "memory");
      n &= 63U;
    }

    auto words = n / sizeof(std::uint64_t);
    if (words != 0) {
      asm volatile("1:\n"
                   "ldr x3, [%[source]], #8\n"
                   "str x3, [%[destination]], #8\n"
                   "subs %[words], %[words], #1\n"
                   "b.ne 1b\n"
                   : [destination] "+r"(d), [source] "+r"(s), [words] "+r"(words)
                   :
                   : "x3", "cc", "memory");
      n &= kWordMask;
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

auto memcmp(const void* lhs, const void* rhs, std::size_t n) noexcept -> int { // NOLINT(readability-identifier-naming)
  // Byte loop: newlib's memcmp uses SIMD registers. Callers here are
  // short string_view compares, so no aligned fast lane is worth it.
  const auto* a = static_cast<const unsigned char*>(lhs);
  const auto* b = static_cast<const unsigned char*>(rhs);
  for (; n != 0; --n, ++a, ++b) {
    if (*a != *b) {
      return *a < *b ? -1 : 1;
    }
  }
  return 0;
}

auto strlen(const char* s) noexcept -> std::size_t { // NOLINT(readability-identifier-naming)
  const char* p = s;
  while (*p != '\0') {
    ++p;
  }
  return static_cast<std::size_t>(p - s);
}

} // extern "C"

namespace nova::memory {

auto restore_changed(void* destination, const void* pristine, std::size_t size) noexcept -> RestoreStats {
  auto*       dst      = static_cast<unsigned char*>(destination);
  const auto* src      = static_cast<const unsigned char*>(pristine);
  const auto  examined = size;
  std::size_t written  = 0;

  if (misalign(dst) == misalign(src)) {
    while (size != 0 && misalign(dst) != 0) {
      if (*dst != *src) {
        *dst = *src;
        ++written;
      }
      ++dst;
      ++src;
      --size;
    }

    auto blocks = size / 64U;
    if (blocks != 0 &&
        ((reinterpret_cast<std::uintptr_t>(dst) | reinterpret_cast<std::uintptr_t>(src)) & kPairMask) == 0) {
      asm volatile("1:\n"
                   "ldp x3, x4, [%[source], #0]\n"
                   "ldp x5, x6, [%[destination], #0]\n"
                   "cmp x3, x5\n"
                   "ccmp x4, x6, #0, eq\n"
                   "b.eq 2f\n"
                   "stp x3, x4, [%[destination], #0]\n"
                   "add %[written], %[written], #16\n"
                   "2:\n"
                   "ldp x3, x4, [%[source], #16]\n"
                   "ldp x5, x6, [%[destination], #16]\n"
                   "cmp x3, x5\n"
                   "ccmp x4, x6, #0, eq\n"
                   "b.eq 3f\n"
                   "stp x3, x4, [%[destination], #16]\n"
                   "add %[written], %[written], #16\n"
                   "3:\n"
                   "ldp x3, x4, [%[source], #32]\n"
                   "ldp x5, x6, [%[destination], #32]\n"
                   "cmp x3, x5\n"
                   "ccmp x4, x6, #0, eq\n"
                   "b.eq 4f\n"
                   "stp x3, x4, [%[destination], #32]\n"
                   "add %[written], %[written], #16\n"
                   "4:\n"
                   "ldp x3, x4, [%[source], #48]\n"
                   "ldp x5, x6, [%[destination], #48]\n"
                   "cmp x3, x5\n"
                   "ccmp x4, x6, #0, eq\n"
                   "b.eq 5f\n"
                   "stp x3, x4, [%[destination], #48]\n"
                   "add %[written], %[written], #16\n"
                   "5:\n"
                   "add %[source], %[source], #64\n"
                   "add %[destination], %[destination], #64\n"
                   "subs %[blocks], %[blocks], #1\n"
                   "b.ne 1b\n"
                   : [destination] "+&r"(dst), [source] "+&r"(src), [blocks] "+&r"(blocks), [written] "+&r"(written)
                   :
                   : "x3", "x4", "x5", "x6", "cc", "memory");
      size &= 63U;
    }

    while (size >= sizeof(std::uint64_t)) {
      const auto value = *reinterpret_cast<const std::uint64_t*>(src);
      auto*      word  = reinterpret_cast<std::uint64_t*>(dst);
      if (*word != value) {
        *word = value;
        written += sizeof(std::uint64_t);
      }
      dst += sizeof(std::uint64_t);
      src += sizeof(std::uint64_t);
      size -= sizeof(std::uint64_t);
    }
  }

  while (size != 0) {
    if (*dst != *src) {
      *dst = *src;
      ++written;
    }
    ++dst;
    ++src;
    --size;
  }
  return {.examined_bytes = examined, .written_bytes = written};
}

} // namespace nova::memory
