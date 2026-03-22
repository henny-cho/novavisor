#pragma once

// novavisor/trap_context.hpp
//
// TrapContext: snapshot of the CPU state at the moment an EL2 exception fires.
//
// This struct is laid out to match the register-save sequence in vec.S exactly.
// Any changes to the field order or padding must be reflected in the assembly
// macros (CTX_* offsets) in vec.S.
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

#include <cstddef>
#include <cstdint>

namespace novavisor {

struct alignas(16) TrapContext {
  std::uint64_t x[31]; // x0-x30 (general-purpose registers)
  std::uint64_t sp;    // SP_EL1 — guest EL1 stack pointer
  std::uint64_t elr;   // ELR_EL2 — exception link register
  std::uint64_t spsr;  // SPSR_EL2 — saved program state register
  std::uint64_t esr;   // ESR_EL2 — exception syndrome register
  std::uint64_t far;   // FAR_EL2 — fault address register
};

static_assert(sizeof(TrapContext) == 288, "TrapContext size must match TRAP_CTX_SIZE in vec.S");
static_assert(alignof(TrapContext) == 16, "TrapContext must be 16-byte aligned for stp/ldp correctness");
static_assert(offsetof(TrapContext, x[30]) == 240, "x[30] offset mismatch");
static_assert(offsetof(TrapContext, sp) == 248, "sp offset mismatch");
static_assert(offsetof(TrapContext, elr) == 256, "elr offset mismatch");
static_assert(offsetof(TrapContext, spsr) == 264, "spsr offset mismatch");
static_assert(offsetof(TrapContext, esr) == 272, "esr offset mismatch");
static_assert(offsetof(TrapContext, far) == 280, "far offset mismatch");

} // namespace novavisor
