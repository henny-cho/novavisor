#pragma once

// nova/arch/trap_context.hpp
//
// TrapContext: snapshot of the CPU state at the moment an EL2 exception fires.
//
// This struct is laid out to match the register-save sequence in vec.S
// exactly. Both sides consume the same offset macros from
// nova/arch/trap_context_offsets.h; change the layout there first.
//
// Layout (288 bytes, 16-byte aligned):
//   Offset   Field     Content
//     0      x[0]      General-purpose register x0
//     8      x[1]      General-purpose register x1
//     ...
//     240    x[30]     Link register (LR) — guest's return address
//     248    sp        SP_EL1: guest EL1 stack pointer
//     256    elr       ELR_EL2: faulting / HVC instruction address
//     264    spsr      SPSR_EL2: guest PSTATE at time of exception
//     272    esr       ESR_EL2: exception syndrome (EC field, ISS, etc.)
//     280    far       FAR_EL2: faulting virtual address (for aborts)

#include "nova/arch/trap_context_offsets.h"

#include <array>
#include <cstddef>
#include <cstdint>

namespace nova {

inline constexpr std::size_t kNumGpRegs    = 31; // x0-x30
inline constexpr std::size_t kTrapCtxAlign = 16; // stp/ldp pair alignment

// Byte offsets and total size, from the header shared with vec.S. The
// static_asserts below pin the struct layout to these values, so a
// drift between C++ and assembly cannot compile.
inline constexpr std::size_t kCtxOffX30   = NOVA_CTX_OFF_X30;
inline constexpr std::size_t kCtxOffSp    = NOVA_CTX_OFF_SP;
inline constexpr std::size_t kCtxOffElr   = NOVA_CTX_OFF_ELR;
inline constexpr std::size_t kCtxOffSpsr  = NOVA_CTX_OFF_SPSR;
inline constexpr std::size_t kCtxOffEsr   = NOVA_CTX_OFF_ESR;
inline constexpr std::size_t kCtxOffFar   = NOVA_CTX_OFF_FAR;
inline constexpr std::size_t kTrapCtxSize = NOVA_TRAP_CTX_SIZE;

struct alignas(kTrapCtxAlign) TrapContext {
  std::array<std::uint64_t, kNumGpRegs> x; // x0-x30 (general-purpose registers)

  std::uint64_t sp;   // SP_EL1 — guest EL1 stack pointer
  std::uint64_t elr;  // ELR_EL2 — exception link register
  std::uint64_t spsr; // SPSR_EL2 — saved program state register
  std::uint64_t esr;  // ESR_EL2 — exception syndrome register
  std::uint64_t far;  // FAR_EL2 — fault address register
};

static_assert(sizeof(TrapContext) == kTrapCtxSize, "TrapContext size must match TRAP_CTX_SIZE in vec.S");
static_assert(alignof(TrapContext) == kTrapCtxAlign, "TrapContext must be 16-byte aligned for stp/ldp correctness");
static_assert(offsetof(TrapContext, x) + ((kNumGpRegs - 1) * sizeof(std::uint64_t)) == kCtxOffX30,
              "x[30] offset mismatch");
static_assert(offsetof(TrapContext, sp) == kCtxOffSp, "sp offset mismatch");
static_assert(offsetof(TrapContext, elr) == kCtxOffElr, "elr offset mismatch");
static_assert(offsetof(TrapContext, spsr) == kCtxOffSpsr, "spsr offset mismatch");
static_assert(offsetof(TrapContext, esr) == kCtxOffEsr, "esr offset mismatch");
static_assert(offsetof(TrapContext, far) == kCtxOffFar, "far offset mismatch");

} // namespace nova
