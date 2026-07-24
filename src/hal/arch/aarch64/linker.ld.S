/*
 * Bare-metal linker script template for the EL2 hypervisor image.
 * Preprocessed with `cc -E -P -x c` (see hal/arch/aarch64/CMakeLists);
 * the RAM window comes from the active board's board_layout.h so this
 * file stays board-agnostic.
 */
#include "hal/board/active/board_layout.h"

ENTRY(_start)

MEMORY
{
    RAM (rwx) : ORIGIN = NOVA_BOARD_RAM_BASE, LENGTH = NOVA_BOARD_RAM_SIZE
}

SECTIONS
{
    .text : {
         KEEP(*(.text.boot))   /* boot.S: _start entry point, must be first */
         . = ALIGN(2048);      /* VBAR_EL2 requires 2^11-byte alignment */
         KEEP(*(.text.vec))    /* vec.S: EL2 exception vector table */
         *(.text*)
    } > RAM

    .rodata : {
        . = ALIGN(8);
        *(.rodata*)
    } > RAM

    .data : {
        . = ALIGN(8);
        *(.data*)
    } > RAM

    .bss (NOLOAD) : {
        . = ALIGN(8);
        __bss_start = .;
        *(.bss*)
        *(COMMON)
        . = ALIGN(8);   /* keep __bss_end 8-byte aligned for boot.S's word loop */
        __bss_end = .;
    } > RAM

    /* One EL2 stack per physical core, stacked downward from
     * __stack_top: core i runs on [top - (i+1)*SIZE, top - i*SIZE). */
    . = ALIGN(16);
    . = . + NOVA_BOARD_EL2_STACK_SIZE * NOVA_BOARD_SMP_CPUS;
    __stack_top = .;
}
