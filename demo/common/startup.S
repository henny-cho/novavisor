// NovaVisor demo EL1 guest startup.
//
// Entered at EL1 with PC = _start, SP undefined, BSS uninitialized.
// We set up the stack, zero BSS, call main(), then issue HVC_EXIT with
// main()'s return value.

.section .text.start, "ax"
.global _start
_start:
    // x0..x3 carry hypervisor-chosen boot args (reserved, ignored for now).

    // Install stack pointer. __stack_top is defined by the linker script.
    ldr     x9, =__stack_top
    mov     sp, x9

    // Zero BSS: [__bss_start, __bss_end). Addresses are 8-byte aligned by
    // the linker script, so a word-at-a-time loop is safe.
    ldr     x9,  =__bss_start
    ldr     x10, =__bss_end
1:
    cmp     x9, x10
    b.ge    2f
    str     xzr, [x9], #8
    b       1b
2:
    // Call main. Demos use the standard `int main(void)` signature.
    bl      main

    // main returned: propagate its int return value as the demo exit code.
    // x0 already holds main's return value per AAPCS64.
    mov     x1, x0                 // x1 = exit code
    mov     x0, #0x1002            // HVC function ID: EXIT
    hvc     #0

    // Should not be reached. If the hypervisor misbehaves, wedge here.
3:
    wfi
    b       3b

.size _start, . - _start
