// boot.S - Hardware Initialization Assembly
//
// Two entry points:
//   _start               — cold boot, primary core only. Secondaries are
//                          held powered off by firmware (PSCI) until the
//                          hypervisor turns them on; a stray core that
//                          reaches _start anyway is parked.
//   nova_secondary_entry — PSCI CPU_ON target for secondary cores.
//                          x0 = this core's index (CPU_ON context id).
//                          Per-core setup only: stack + vectors. BSS and
//                          all shared state were initialized by the
//                          primary before CPU_ON was issued.

#include "hal/board/active/board_layout.h"

.global _start
.section .text.boot

_start:
    // Read current exception level (CurrentEL is bits 3:2)
    mrs x0, CurrentEL
    lsr x0, x0, #2
    cmp x0, #2

    // If not EL2 (Hypervisor mode), hang
    b.ne .Lhang

    // Park any core that is not the primary — cold-boot init (BSS zero,
    // table builds) must run exactly once. Aff0 is the core index on
    // this board.
    mrs x0, mpidr_el1
    and x0, x0, #0xFF
    cbnz x0, .Lhang

    // Set up stack pointer for EL2
    ldr x0, =__stack_top
    mov sp, x0

    // Install exception vector table
    ldr x0, =_vector_table
    msr vbar_el2, x0
    isb

    // Zero BSS: [__bss_start, __bss_end). Must complete before the first
    // C++ statement runs — static state (Stage 2 tables, Vcpu, cib
    // internals) lives there. Both markers are 8-byte aligned by the
    // linker script, so a word-at-a-time loop is safe.
    ldr x0, =__bss_start
    ldr x1, =__bss_end
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

.global nova_secondary_entry
.type   nova_secondary_entry, @function

nova_secondary_entry:
    // x0 = core index. Carve this core's stack out of the shared
    // region: SP = __stack_top - index * stack size.
    ldr x1, =__stack_top
    mov x2, #NOVA_BOARD_EL2_STACK_SIZE
    mul x2, x2, x0
    sub x1, x1, x2
    mov sp, x1

    ldr x1, =_vector_table
    msr vbar_el2, x1
    isb

    bl novavisor_secondary
    b .Lhang
.size nova_secondary_entry, . - nova_secondary_entry
