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

    // Zero BSS: [bss_start, bss_end). Must complete before the first
    // C++ statement runs — static state (Stage 2 tables, Vcpu, cib
    // internals) lives there. Both markers are 8-byte aligned by the
    // linker script, so a word-at-a-time loop is safe.
    ldr x0, =bss_start
    ldr x1, =bss_end
1:
    cmp x0, x1
    b.ge 2f
    str xzr, [x0], #8
    b 1b
2:
    // Branch to C++ main entry
    bl novavisor_main

.Lhang:
    wfi
    b .Lhang
