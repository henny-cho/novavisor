// components/core_vcpu/src/core_vcpu.cpp
//
// Seeds vcpu 0 from the guest table injected by the project composition
// (nova/guest.hpp) and performs the first ERET into EL1.

#include "components/core_vcpu/include/core_vcpu.hpp"

#include "nova/guest.hpp"

#include <cstdint>

namespace nova {

// Defined in hal/armv8_aarch64/vcpu_enter.S.
extern "C" [[noreturn]] void nova_vcpu_enter(std::uint64_t entry_pc, std::uint64_t sp_el1,
                                             std::uint64_t spsr_el2) noexcept;

namespace vcpu {
namespace {

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

// Phase 5/6: single VCPU. Phase 7 sizes the backing store from the
// guest table and adds a scheduler-owned "current" pointer.
Vcpu g_vcpu0;

} // namespace

auto current() noexcept -> Vcpu& {
  return g_vcpu0;
}

[[noreturn]] void enter_guest() noexcept {
  const GuestDescriptor& guest = guest_table().front();

  Vcpu& vcpu = g_vcpu0;
  vcpu.guest = &guest;
  vcpu.elr   = guest.entry_pc;
  vcpu.sp    = guest.stack_top;
  vcpu.spsr  = kSpsrEl1h;
  vcpu.state = Vcpu::State::kRunning;

  nova_vcpu_enter(vcpu.elr, vcpu.sp, vcpu.spsr);
}

} // namespace vcpu
} // namespace nova
