// hal/arch/aarch64/lib/restore.cpp
//
// Warm-reset pristine restore: rewrite a guest window from its
// snapshot while skipping stores for blocks that already match. The
// skip is what keeps a restore practical under TCG, where a blind copy
// of the whole window dominates the reboot latency.

#include "hal/arch/aarch64/lib/word.hpp"
#include "hal/mem.hpp"

#include <cstddef>
#include <cstdint>

namespace nova::memory {

auto restore_changed(void* destination, const void* pristine, std::size_t size) noexcept -> RestoreStats {
  auto*       dst      = static_cast<unsigned char*>(destination);
  const auto* src      = static_cast<const unsigned char*>(pristine);
  const auto  examined = size;
  std::size_t written  = 0;

  if (arch::word::misalign(dst) == arch::word::misalign(src)) {
    while (size != 0 && arch::word::misalign(dst) != 0) {
      if (*dst != *src) {
        *dst = *src;
        ++written;
      }
      ++dst;
      ++src;
      --size;
    }

    auto blocks = size / 64U;
    if (blocks != 0 && ((reinterpret_cast<std::uintptr_t>(dst) | reinterpret_cast<std::uintptr_t>(src)) &
                        arch::word::kPairMask) == 0) {
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
