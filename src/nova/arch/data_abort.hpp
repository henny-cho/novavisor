#pragma once

// nova/arch/data_abort.hpp
//
// Data Abort syndrome decode and MMIO access reconstruction — the pure
// half of the Stage 2 MMIO trap path (the dispatch lives in
// trap_handler). Split from esr.hpp: generic EC routing is consumed by
// the trap router, while everything here is consumed only by the
// data-abort/MMIO emulation path.
//
// Reference: Arm ARM D17.2.37, "ISS encoding for an exception from a
// Data Abort". Only the fields the MMIO emulation path consumes are
// decoded; ISV must be 1 for SAS/SSE/SRT/SF to be meaningful (single
// general-purpose-register load/store without writeback).
//
// This header has no dependencies beyond <cstdint> and is safe to
// include in host-side GTest builds.

#include <cstdint>

namespace nova::esr {

inline constexpr std::uint64_t kDaIsvBit   = 1ULL << 24U; // syndrome valid
inline constexpr std::uint64_t kDaSasShift = 22U;         // access size = 2^SAS bytes
inline constexpr std::uint64_t kDaSasMask  = 0x3U;
inline constexpr std::uint64_t kDaSseBit   = 1ULL << 21U; // sign-extend the load
inline constexpr std::uint64_t kDaSrtShift = 16U;         // transfer register number
inline constexpr std::uint64_t kDaSrtMask  = 0x1FU;
inline constexpr std::uint64_t kDaSfBit    = 1ULL << 15U; // 64-bit register width
inline constexpr std::uint64_t kDaS1ptwBit = 1ULL << 7U;  // fault on a Stage 1 walk
inline constexpr std::uint64_t kDaWnrBit   = 1ULL << 6U;  // write, not read
inline constexpr std::uint64_t kDaDfscMask = 0x3FU;       // fault status code
inline constexpr std::uint32_t kSrtZeroReg = 31U;         // SRT 31 = xzr/wzr

// DFSC 0b0001LL = Translation fault, level LL (0..3) — the only faults
// the MMIO path emulates (an unmapped IPA on purpose).
inline constexpr std::uint8_t kDfscTranslationBase = 0x04U;

struct DataAbort {
  bool          isv         = false;
  std::uint32_t size        = 0; // access size in bytes (1/2/4/8)
  bool          sign_extend = false;
  std::uint32_t srt         = 0; // transfer register (kSrtZeroReg = xzr/wzr)
  bool          sixty_four  = false;
  bool          s1ptw       = false;
  bool          write       = false;
  std::uint8_t  dfsc        = 0;
};

[[nodiscard]] inline auto parse_data_abort(std::uint64_t esr) noexcept -> DataAbort {
  return DataAbort{
      .isv         = (esr & kDaIsvBit) != 0U,
      .size        = 1U << ((esr >> kDaSasShift) & kDaSasMask),
      .sign_extend = (esr & kDaSseBit) != 0U,
      .srt         = static_cast<std::uint32_t>((esr >> kDaSrtShift) & kDaSrtMask),
      .sixty_four  = (esr & kDaSfBit) != 0U,
      .s1ptw       = (esr & kDaS1ptwBit) != 0U,
      .write       = (esr & kDaWnrBit) != 0U,
      .dfsc        = static_cast<std::uint8_t>(esr & kDaDfscMask),
  };
}

[[nodiscard]] inline constexpr auto is_translation_fault(std::uint8_t dfsc) noexcept -> bool {
  return (dfsc & ~0x3U) == kDfscTranslationBase;
}

// ---------------------------------------------------------------------------
// Fault IPA composition
//
// HPFAR_EL2 bits 43:4 hold IPA bits 51:12 of the faulting access;
// FAR_EL2 supplies the page offset. FAR alone would be the guest VA —
// wrong once a guest enables its own Stage 1 MMU.
// ---------------------------------------------------------------------------

inline constexpr std::uint64_t kHpfarFipaMask  = 0x0000'0FFF'FFFF'FFF0ULL; // bits 43:4
inline constexpr std::uint64_t kHpfarFipaShift = 4U;
inline constexpr std::uint64_t kPageOffsetMask = 0xFFFULL;
inline constexpr std::uint64_t kPageShift      = 12U;

[[nodiscard]] inline constexpr auto fault_ipa(std::uint64_t hpfar, std::uint64_t far) noexcept -> std::uint64_t {
  return (((hpfar & kHpfarFipaMask) >> kHpfarFipaShift) << kPageShift) | (far & kPageOffsetMask);
}

// Widen an emulated MMIO read result the way the faulting load would
// have: truncate to the access size, sign-extend when SSE, and clamp to
// 32 bits for a W-register (SF=0) destination.
[[nodiscard]] inline constexpr auto extend_mmio_read(std::uint64_t value, std::uint32_t size, bool sign_extend,
                                                     bool sixty_four) noexcept -> std::uint64_t {
  constexpr std::uint32_t kBitsPerByte = 8U;
  constexpr std::uint64_t kWordMask    = 0xFFFF'FFFFULL;
  const std::uint32_t     bits         = size * kBitsPerByte;
  const std::uint64_t     mask         = (size >= sizeof(std::uint64_t)) ? ~0ULL : (1ULL << bits) - 1U;

  std::uint64_t result = value & mask;
  if (sign_extend && size < sizeof(std::uint64_t) && ((result >> (bits - 1U)) & 1U) != 0U) {
    result |= ~mask;
  }
  return sixty_four ? result : (result & kWordMask);
}

} // namespace nova::esr
