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

#include "hal/arch/aarch64/stage1_regs.h"
#include "hal/board/active/board_layout.h"
#include "nova/arch/mpidr.h"

.global _start
.section .text.boot

_start:
    // Read current exception level (CurrentEL is bits 3:2)
    mrs x0, CurrentEL
    lsr x0, x0, #2
    cmp x0, #2

    // If not EL2 (Hypervisor mode), hang
    b.ne .Lhang

    // Park any core that is not the board's boot PE. Compare all four
    // affinity levels so a second cluster's Aff0=0 cannot enter twice.
    mrs x0, mpidr_el1
    ldr x1, =NOVA_MPIDR_AFFINITY_MASK
    and x0, x0, x1
    ldr x1, =NOVA_BOARD_BOOT_MPIDR
    cmp x0, x1
    b.ne .Lhang

    // Publish the dense core index for cpu::id() — the boot PE is
    // kCpuAffinity[0] by definition. TPIDR_EL2 is otherwise unused.
    msr tpidr_el2, xzr

    // Discard whatever firmware left in the cache hierarchy before any
    // RAM state EL2 relies on is written with the MMU still off — a
    // stale dirty line evicting later would silently clobber it.
    bl  nova_dcache_inv_all

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
    // Build the EL2 Stage-1 identity map and enable MMU + caches.
    // Must follow BSS zeroing (the tables live there) and precede
    // everything else: from here on EL2 runs cacheable and W^X.
    bl nova_el2_mmu_init

    // Branch to C++ main entry
    bl novavisor_main

.Lhang:
    wfi
    b .Lhang

.global nova_secondary_entry
.type   nova_secondary_entry, @function

nova_secondary_entry:
    // x0 = core index. Publish it for cpu::id() before anything else
    // runs on this core.
    msr tpidr_el2, x0
    mov x9, x0

    // Enable this core's Stage-1 regime before touching the stack or
    // any shared data: reading a runtime-set global here would be a
    // non-cacheable access racing the primary's cached writes, so the
    // root symbol and register values are link/compile-time constants.
    // PSCI hands the core over with its local caches invalidated; the
    // table walk itself is cacheable and therefore coherent.
    ldr x0, =nova_el2_l1_root
    ldr x1, =NOVA_EL2_TCR
    ldr x2, =NOVA_EL2_MAIR
    ldr x3, =NOVA_EL2_SCTLR
    bl  nova_el2_stage1_enable

    // Carve the core's stack out of the shared region:
    // SP = __stack_top - index * stack size.
    ldr x1, =__stack_top
    mov x2, #NOVA_BOARD_EL2_STACK_SIZE
    mul x2, x2, x9
    sub x1, x1, x2
    mov sp, x1
    mov x0, x9

    ldr x1, =_vector_table
    msr vbar_el2, x1
    isb

    bl novavisor_secondary
    b .Lhang
.size nova_secondary_entry, . - nova_secondary_entry
