// hal/arch/aarch64/vcpu_enter.S
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
//
// All GP registers are zeroed before ERET so the boot guest starts from
// the same clean state a scheduler switch-in provides (and no EL2
// values leak into EL1).

.global nova_vcpu_enter
.type   nova_vcpu_enter, @function
.section .text.nova_vcpu_enter

nova_vcpu_enter:
    msr     sp_el1,   x1
    msr     spsr_el2, x2
    msr     elr_el2,  x0

    .irp reg, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, \
              16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30
    mov     x\reg, xzr
    .endr

    isb
    eret
.size nova_vcpu_enter, . - nova_vcpu_enter
