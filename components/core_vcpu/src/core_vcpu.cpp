// components/core_vcpu/src/core_vcpu.cpp
//
// Seeds the first (and in Phase 5, only) EL1 entry from the static
// guest descriptor in projects/qemu_virt_arm64/include/guest_config.hpp.

#include "components/core_vcpu/include/core_vcpu.hpp"

#include "projects/qemu_virt_arm64/include/guest_config.hpp"

#include <cstdint>

namespace nova {

// Defined in hal/armv8_aarch64/vcpu_enter.S.
extern "C" [[noreturn]] void nova_vcpu_enter(std::uint64_t entry_pc, std::uint64_t sp_el1,
                                             std::uint64_t spsr_el2) noexcept;

namespace vcpu {

// SPSR_EL2 value to restore on ERET into the fresh guest:
//   M[3:0]   = 0b0101  EL1h  (EL1, using SP_EL1)
//   M[4]     = 0       AArch64 execution state
//   F (b6)   = 1       FIQ masked
//   I (b7)   = 1       IRQ masked
//   A (b8)   = 1       SError masked
//   D (b9)   = 1       Debug masked
//   others   = 0
// Phase 6 will clear I/F when vGIC injection comes online.
inline constexpr std::uint64_t kSpsrEl1h = 0x3C5ULL;

[[noreturn]] void enter_guest() noexcept {
  nova_vcpu_enter(qemu_virt::kGuestEntry, qemu_virt::kGuestStackTop, kSpsrEl1h);
}

} // namespace vcpu
} // namespace nova
