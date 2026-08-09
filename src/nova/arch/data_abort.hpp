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
// Depends only on esr.hpp (shared ISS decode constants) and <cstdint>,
// so it stays safe to include in host-side GTest builds.

#include "nova/arch/esr.hpp"

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

[[nodiscard]] constexpr auto parse_data_abort(std::uint64_t esr) noexcept -> DataAbort {
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

// The syndrome the emulation path reads back, composed from the field
// positions above and decoded again. The two shapes that matter are a
// register-width load and a store from the zero register.
static_assert(
    [] {
      const auto iss = [](bool isv, std::uint64_t sas, bool sse, std::uint64_t srt, bool sf, bool s1ptw, bool wnr,
                          std::uint64_t dfsc) {
        return (isv ? kDaIsvBit : 0U) | (sas << kDaSasShift) | (sse ? kDaSseBit : 0U) | (srt << kDaSrtShift) |
               (sf ? kDaSfBit : 0U) | (s1ptw ? kDaS1ptwBit : 0U) | (wnr ? kDaWnrBit : 0U) | dfsc;
      };
      // ldr w3, [x]: SAS 2 is a 4-byte access into a W register, translation fault at level 3.
      const DataAbort load = parse_data_abort(iss(true, 2, false, 3, false, false, false, 0x07));
      // ldrsh x0: the same access sign-extended through a 64-bit register.
      const DataAbort signed_load = parse_data_abort(iss(true, 1, true, 0, true, false, false, 0x07));
      // str xzr: SAS 3 is 8 bytes, and SRT 31 names the zero register rather than x31.
      const DataAbort store = parse_data_abort(iss(true, 3, false, 31, true, false, true, 0x04));
      // A fault on a Stage 1 walk carries no transfer to reconstruct.
      const DataAbort walk = parse_data_abort(iss(false, 0, false, 0, false, true, false, 0x05));
      return load.isv && load.size == 4 && !load.sign_extend && load.srt == 3 && !load.sixty_four && !load.write &&
             !load.s1ptw && is_translation_fault(load.dfsc) &&                             //
             signed_load.size == 2 && signed_load.sign_extend && signed_load.sixty_four && //
             store.size == 8 && store.srt == kSrtZeroReg && store.sixty_four && store.write &&
             is_translation_fault(store.dfsc) && //
             !walk.isv && walk.s1ptw;
    }(),
    "the data abort syndrome names the access the MMIO path has to replay");

// DFSC 0b0001LL is a translation fault at level LL and nothing else is.
static_assert(
    [] {
      return is_translation_fault(0x04) && is_translation_fault(0x05) && // levels 0 and 1
             is_translation_fault(0x06) && is_translation_fault(0x07) && // levels 2 and 3
             !is_translation_fault(0x00) &&                              // address size fault
             !is_translation_fault(0x0D) &&                              // permission fault, level 1
             !is_translation_fault(0x21);                                // alignment fault
    }(),
    "only an unmapped IPA reaches the MMIO emulation path");

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

// The page from HPFAR_EL2 and the offset from FAR_EL2, with everything
// each register carries outside its contribution discarded.
static_assert(
    [] {
      const auto hpfar_of = [](std::uint64_t ipa) { return (ipa >> kPageShift) << kHpfarFipaShift; };
      return fault_ipa(hpfar_of(0x0800'0104), 0xFFFF'0000'0000'0104) ==
                 0x0800'0104 && // FAR contributes the offset only
             fault_ipa((1ULL << 63U) | hpfar_of(0x080A'0000) | 0xF, 0) == 0x080A'0000; // HPFAR's reserved bits do not
    }(),
    "the faulting IPA is the guest's address, not the guest's virtual address");

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
