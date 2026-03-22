// boot.S - Hardware Initialization Assembly

.global _start
.section .text.boot

_start:
    // Read current exception level (CurrentEL is bits 3:2)
    mrs x0, CurrentEL
    lsr x0, x0, #2
    cmp x0, #2

    // If not EL2 (Hypervisor mode), hang
    b.ne .Lhang

    // Set up stack pointer for EL2
    ldr x0, =__stack_top
    mov sp, x0

    // Install exception vector table
    ldr x0, =_vector_table
    msr vbar_el2, x0
    isb

    // Branch to C++ main entry
    bl novavisor_main

.Lhang:
    wfi
    b .Lhang
