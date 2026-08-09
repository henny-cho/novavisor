#pragma once

// nova/arch/cpu_contract.hpp
//
// Boot-time CPU contract: the translation parameters the hypervisor
// hardcodes (VTCR_EL2 PS and the 4 KiB stage-2 granule) must be
// implemented by the silicon. Programming an unimplemented PS or
// granule is CONSTRAINED UNPREDICTABLE — the hardware never reports
// it, so the boot path validates the ID registers and refuses to run
// with a diagnosable reason instead. Pure and host-testable; the raw
// register value comes in through the hal facade.

#include <cstdint>
#include <string_view>

namespace nova::arch {

enum class CpuContractError : std::uint8_t {
  kNone,
  kPaRangeTooSmall, // ID_AA64MMFR0_EL1.PARange below the configured VTCR_EL2.PS
  kNoStage2Gran4,   // 4 KiB granule not implemented for stage-2 walks
};

// ID_AA64MMFR0_EL1 fields. PARange encodings order by size (0b0000 =
// 32-bit .. 0b0110 = 52-bit) and VTCR_EL2.PS uses the same encoding,
// so the two compare directly.
[[nodiscard]] constexpr auto mmfr0_pa_range(std::uint64_t mmfr0) noexcept -> std::uint64_t {
  return mmfr0 & 0xFU;
}

// TGran4 (bits 31:28): 0b1111 = 4 KiB not implemented at stage 1.
// TGran4_2 (bits 43:40): stage-2 override — 0b0000 follows TGran4,
// 0b0001 forbids 4 KiB at stage 2, anything else grants it.
[[nodiscard]] constexpr auto mmfr0_stage2_gran4(std::uint64_t mmfr0) noexcept -> bool {
  const std::uint64_t tgran4   = (mmfr0 >> 28U) & 0xFU;
  const std::uint64_t tgran4_2 = (mmfr0 >> 40U) & 0xFU;
  if (tgran4_2 == 0U) {
    return tgran4 != 0xFU;
  }
  return tgran4_2 != 1U;
}

[[nodiscard]] constexpr auto validate_cpu_contract(std::uint64_t mmfr0, std::uint64_t required_pa_range) noexcept
    -> CpuContractError {
  if (mmfr0_pa_range(mmfr0) < required_pa_range) {
    return CpuContractError::kPaRangeTooSmall;
  }
  if (!mmfr0_stage2_gran4(mmfr0)) {
    return CpuContractError::kNoStage2Gran4;
  }
  return CpuContractError::kNone;
}

// The gate against the ID register it reads. The encoder here is the
// field layout named above: PARange [3:0], TGran4 [31:28], TGran4_2
// [43:40]; the required PARange is 0b010, the 40-bit VTCR_EL2.PS both
// boards configure.
static_assert(
    [] {
      const auto mmfr0 = [](std::uint64_t pa_range, std::uint64_t tgran4, std::uint64_t tgran4_2) {
        return pa_range | (tgran4 << 28U) | (tgran4_2 << 40U);
      };
      const std::uint64_t pa40 = 0b010;
      return validate_cpu_contract(mmfr0(0b010, 0, 0), pa40) == CpuContractError::kNone && // exactly enough
             validate_cpu_contract(mmfr0(0b101, 0, 0), pa40) == CpuContractError::kNone && // more than enough
             validate_cpu_contract(mmfr0(0b001, 0, 0), pa40) == CpuContractError::kPaRangeTooSmall &&
             validate_cpu_contract(mmfr0(0b000, 0, 0), pa40) == CpuContractError::kPaRangeTooSmall &&
             // TGran4_2 == 0 defers to TGran4, where 0b1111 means the granule is absent.
             validate_cpu_contract(mmfr0(0b101, 0x0, 0x0), pa40) == CpuContractError::kNone &&
             validate_cpu_contract(mmfr0(0b101, 0xF, 0x0), pa40) == CpuContractError::kNoStage2Gran4 &&
             // A non-zero TGran4_2 overrides it in both directions.
             validate_cpu_contract(mmfr0(0b101, 0x0, 0x1), pa40) == CpuContractError::kNoStage2Gran4 &&
             validate_cpu_contract(mmfr0(0b101, 0xF, 0x2), pa40) == CpuContractError::kNone;
    }(),
    "the boot gate refuses exactly the silicon the hardcoded translation parameters would misprogram");

[[nodiscard]] constexpr auto to_string(CpuContractError error) noexcept -> std::string_view {
  switch (error) {
  case CpuContractError::kPaRangeTooSmall:
    return "PARange below configured VTCR_EL2.PS";
  case CpuContractError::kNoStage2Gran4:
    return "4 KiB stage-2 granule not implemented";
  case CpuContractError::kNone:
    return "ok";
  }
  return "unknown";
}

} // namespace nova::arch
