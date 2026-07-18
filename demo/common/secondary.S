// NovaVisor demo EL1 secondary vCPU entry (opt-in source for SMP demos).
//
// PSCI CPU_ON lands here with x0 = context_id; the caller passes this
// vCPU's stack top there (PSCI leaves SP undefined). BSS is NOT
// touched — the boot vCPU initialized it, and re-zeroing would race
// the running primary. FP access is enabled exactly like _start.

#include "nova/abi/psci.h"

.section .text, "ax"
.global _secondary_start
_secondary_start:
    mov     sp, x0

    mov     x9, #(0x3 << 20)       // CPACR_EL1.FPEN = 0b11
    msr     cpacr_el1, x9
    isb

    bl      secondary_main

    // secondary_main returned: power this vCPU off (does not return).
    ldr     x0, =PSCI_FN_CPU_OFF
    hvc     #0
1:
    wfi
    b       1b

.size _secondary_start, . - _secondary_start
