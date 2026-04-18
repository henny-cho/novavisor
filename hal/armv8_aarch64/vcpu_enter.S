// hal/armv8_aarch64/vcpu_enter.S
//
// One-way transition from EL2 into an EL1 guest.
//
//   extern "C" [[noreturn]]
//   void nova_vcpu_enter(uint64_t entry_pc, uint64_t sp_el1, uint64_t spsr_el2);
//     x0 = guest PC         → ELR_EL2
//     x1 = initial SP_EL1   → SP_EL1
//     x2 = PSTATE to restore → SPSR_EL2 (EL1h + DAIF masked, AArch64)
//
// Control returns to EL2 only through a hardware trap (vec.S path),
// never by returning from this function. The EL2 caller's stack frame
// below SP_EL2 is effectively dead after ERET; future trap handlers
// reuse the same stack top and unwind cleanly on epilogue.

.global nova_vcpu_enter
.type   nova_vcpu_enter, @function
.section .text.nova_vcpu_enter

nova_vcpu_enter:
    msr     sp_el1,   x1
    msr     spsr_el2, x2
    msr     elr_el2,  x0
    isb
    eret
.size nova_vcpu_enter, . - nova_vcpu_enter
